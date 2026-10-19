"""Supervise one fresh Python process per replay, isolating worker failures."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import psutil


class WorkerInterrupted(RuntimeError):
    """An execution interruption, never evidence that replay labels are low quality."""

    def __init__(self, reason, exitcode=None, attempt_dir=None):
        self.reason = str(reason)
        self.exitcode = exitcode
        self.attempt_dir = Path(attempt_dir) if attempt_dir is not None else None
        super().__init__(f"{self.reason}; exitcode={exitcode}; attempt_dir={self.attempt_dir}")


def _write_json(path, value):
    temporary = path.with_name(path.name + ".next")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(100):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.05)


def _identity(task):
    return {
        "sha256": task["row"]["sha256"],
        "identity": task["row"]["identity"],
        "split": task["row"]["bc_split"],
        "unit": str(Path(task["unit"]).resolve()),
    }


def _validate_task(task, project_root):
    project_root = Path(project_root).resolve()
    if Path(task["root"]).resolve() != project_root:
        raise ValueError("Task root does not match executor project root")
    unit = Path(task["unit"]).resolve()
    processed = project_root / "data/processed"
    if unit == processed or not unit.is_relative_to(processed):
        raise ValueError("Task unit must be below project data/processed")
    digest = task["row"]["sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Task needs a lowercase replay SHA-256")
    if task["row"]["bc_split"] not in ("train", "validation", "test"):
        raise ValueError("Invalid task split")
    for name in ("game_version", "base_build", "data_build", "data_version"):
        if task["row"]["identity"].get(name) in (None, ""):
            raise ValueError(f"Missing task engine identity: {name}")
    return unit


def _completed_unit(unit):
    # Delayed imports avoid a cycle when bc_batch imports this executor.
    from scv_star.data.bc_batch import completed_unit

    return completed_unit(unit)


def _verify_result(task, result):
    if not isinstance(result, dict) or result.get("status") not in (
        "accepted",
        "quality_excluded",
        "failed",
    ):
        raise ValueError("Invalid worker result status")
    if (
        result.get("sha256") != task["row"]["sha256"]
        or result.get("build") != task["row"]["identity"]["base_build"]
        or result.get("split", task["row"]["bc_split"]) != task["row"]["bc_split"]
    ):
        raise ValueError("Worker result replay/build/split identity mismatch")
    if result["status"] == "accepted":
        completed = _completed_unit(Path(task["unit"]).resolve())
        if completed is None or any(result.get(key) != value for key, value in completed.items()):
            raise ValueError("Accepted receipt disagrees with the frozen completed unit")
        if (
            completed["sha256"] != task["row"]["sha256"]
            or completed["build"] != task["row"]["identity"]["base_build"]
            or completed["split"] != task["row"]["bc_split"]
        ):
            raise ValueError("Completed unit does not match the submitted task")
    return result


def _worker_command(task_path, result_path):
    return [
        sys.executable,
        "-B",
        "-m",
        "scv_star.data.bc_isolated",
        "--task",
        str(task_path),
        "--result",
        str(result_path),
    ]


def _owned_temp_dir(command, unit):
    allowed = {
        (unit / "screen-client/temp").resolve(),
        (unit / "replay/client/temp").resolve(),
    }
    values = [
        Path(command[index + 1].strip('"')).resolve()
        for index, argument in enumerate(command[:-1])
        if argument.lower() == "-tempdir"
    ]
    return values[0] if len(values) == 1 and values[0] in allowed else None


def _cleanup_sc2(unit):
    """Terminate only engines naming this exact unit's two permitted temp dirs."""
    evidence, owned = [], []
    for process in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            info = process.info
            if str(info.get("name", "")).lower() not in ("sc2_x64.exe", "sc2.exe"):
                continue
            temporary = _owned_temp_dir(info.get("cmdline") or [], unit)
            if temporary is None:
                continue
            record = {
                "pid": info["pid"],
                "create_time": info["create_time"],
                "temp_dir": str(temporary),
                "status": "terminating_owned_orphan",
            }
            evidence.append(record)
            # psutil also checks PID reuse when terminating a Process instance.
            if process.create_time() != info["create_time"]:
                record["status"] = "pid_changed_not_touched"
                continue
            process.terminate()
            owned.append((process, record))
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as error:
            if evidence and evidence[-1]["pid"] == process.pid:
                evidence[-1].update(status="cleanup_failed", error=str(error))
    if owned:
        _, remaining = psutil.wait_procs([p for p, _ in owned], timeout=5)
        for process, record in owned:
            try:
                if process in remaining:
                    process.kill()
                    process.wait(timeout=5)
                record["status"] = "owned_orphan_exited"
            except psutil.NoSuchProcess:
                record["status"] = "owned_orphan_exited"
            except (psutil.AccessDenied, psutil.TimeoutExpired) as error:
                record.update(status="cleanup_failed", error=str(error))
    return evidence


def _stop_worker(process):
    # Windows venv python.exe is a redirector: Popen.pid can own a second Python.
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    interpreters = []
    for child in children:
        try:
            if child.name().lower().startswith("python"):
                interpreters.append(child)
                child.terminate()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    _, pending = psutil.wait_procs(interpreters, timeout=5)
    for child in pending:
        child.kill()
        child.wait(timeout=5)
    return [child.pid for child in interpreters]


class IsolatedExecutor:
    """Threads supervise independent children; no multiprocessing pool is shared."""

    def __init__(self, max_workers, audit_root, project_root, timeout_seconds=3600):
        if max_workers < 1 or not 0 < timeout_seconds <= 3600:
            raise ValueError("Invalid worker count or timeout (maximum 3600 seconds)")
        self.project_root = Path(project_root).resolve()
        self.audit_root = Path(audit_root).resolve()
        if not self.audit_root.is_relative_to(self.project_root / "artifacts"):
            raise ValueError("Worker audit root must be under project artifacts")
        self.audit_root.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bc-supervisor")
        self._lock = threading.Lock()
        self._units = set()

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.shutdown()

    def shutdown(self, wait=True, *, cancel_futures=False):
        self._pool.shutdown(wait=wait, cancel_futures=cancel_futures)

    def submit(self, task):
        # Snapshot input before submitting: later registry edits cannot change a task.
        task = json.loads(json.dumps(task))
        unit = _validate_task(task, self.project_root)
        with self._lock:
            if unit in self._units:
                raise ValueError("The same unit is already submitted to this executor")
            self._units.add(unit)
        try:
            future = self._pool.submit(self._run, task, unit)
        except Exception:
            with self._lock:
                self._units.discard(unit)
            raise

        def discard_cancelled(completed):
            if completed.cancelled():
                with self._lock:
                    self._units.discard(unit)

        future.add_done_callback(discard_cancelled)
        return future

    def _run(self, task, unit):
        attempt = self.audit_root / (
            task["row"]["sha256"][:12] + "-" + str(time.time_ns()) + "-" + uuid.uuid4().hex[:8]
        )
        try:
            return self._execute(task, unit, attempt)
        except WorkerInterrupted:
            raise
        except Exception as error:
            raise WorkerInterrupted(f"{type(error).__name__}: {error}", None, attempt) from error
        finally:
            with self._lock:
                self._units.discard(unit)

    def _execute(self, task, unit, attempt):
        attempt.mkdir()
        task_path, receipt_path = attempt / "task.json", attempt / "result.json"
        _write_json(task_path, task)
        task_hash = hashlib.sha256(task_path.read_bytes()).hexdigest()
        identity = _identity(task)
        audit = {
            "status": "starting",
            "task_sha256": task_hash,
            "task_identity": identity,
            "timeout_seconds": self.timeout_seconds,
        }
        audit_path = attempt / "execution.json"
        process = None
        started = time.monotonic()
        try:
            _write_json(audit_path, audit)
            environment = os.environ.copy()
            environment.update(
                PYTHONFAULTHANDLER="1",
                PYTHONUTF8="1",
                PYTHONIOENCODING="utf-8",
                PYTHONUNBUFFERED="1",
                PYTHONDONTWRITEBYTECODE="1",
            )
            source_paths = [
                str(self.project_root / "src"),
                str(Path(__file__).resolve().parents[3] / "src"),
            ]
            if environment.get("PYTHONPATH"):
                source_paths.append(environment["PYTHONPATH"])
            environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(source_paths))
            with (attempt / "stdout.log").open("wb") as log:
                process = subprocess.Popen(
                    _worker_command(task_path, receipt_path),
                    cwd=self.project_root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                audit.update(status="running", pid=process.pid, started_utc=time.time())
                _write_json(audit_path, audit)
                try:
                    process.wait(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    audit["terminated_python_descendants"] = _stop_worker(process)
                    raise WorkerInterrupted("Worker deadline exceeded", process.returncode, attempt)
            if process.returncode != 0:
                raise WorkerInterrupted(
                    "Worker exited without normal completion", process.returncode, attempt
                )
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if (
                    receipt["task_sha256"] != task_hash
                    or receipt["task_identity"] != identity
                    or not (
                        receipt["pid"] == process.pid
                        or (os.name == "nt" and receipt.get("ppid") == process.pid)
                    )
                    or hashlib.sha256(task_path.read_bytes()).hexdigest() != task_hash
                ):
                    raise ValueError("Receipt task/PID identity mismatch")
                result = _verify_result(task, receipt["result"])
                audit.update(worker_pid=receipt["pid"], worker_parent_pid=receipt.get("ppid"))
            except Exception as error:
                raise WorkerInterrupted(
                    f"Invalid or missing worker receipt: {error}", process.returncode, attempt
                ) from error
            audit.update(status="completed", result_status=result["status"])
            return result
        except WorkerInterrupted as error:
            audit.update(status="infra_interrupted", reason=error.reason)
            raise
        except Exception as error:
            audit.update(status="infra_interrupted", reason=f"{type(error).__name__}: {error}")
            raise WorkerInterrupted(
                audit["reason"], process.returncode if process else None, attempt
            ) from error
        finally:
            if process is not None and process.poll() is None:
                audit["terminated_python_descendants"] = _stop_worker(process)
            cleanup = _cleanup_sc2(unit)
            cleanup_failed = any(record["status"] == "cleanup_failed" for record in cleanup)
            if cleanup_failed:
                audit.update(status="infra_interrupted", reason="Owned SC2 cleanup failed")
            audit.update(
                exitcode=process.returncode if process else None,
                seconds=time.monotonic() - started,
                cleanup=cleanup,
            )
            _write_json(audit_path, audit)
            if cleanup_failed:
                raise WorkerInterrupted(
                    audit["reason"], process.returncode if process else None, attempt
                )


def run_single(task_path, result_path):
    task_path, result_path = Path(task_path).resolve(), Path(result_path).resolve()
    if task_path.parent != result_path.parent or result_path.exists():
        raise ValueError("Receipt must be a new file beside its task")
    payload = task_path.read_bytes()
    task = json.loads(payload)
    _validate_task(task, task["root"])
    try:
        from scv_star.data.bc_batch import run_candidate

        result = _verify_result(task, run_candidate(task))
    except Exception as error:
        result = {
            "status": "failed",
            "sha256": task["row"]["sha256"],
            "build": task["row"]["identity"]["base_build"],
            "error": f"{type(error).__name__}: {error}",
        }
    _write_json(
        result_path,
        {
            "task_sha256": hashlib.sha256(payload).hexdigest(),
            "task_identity": _identity(task),
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "result": result,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    run_single(args.task, args.result)


if __name__ == "__main__":
    main()
