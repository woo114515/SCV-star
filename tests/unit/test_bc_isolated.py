import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scv_star.data.bc_isolated import IsolatedExecutor, WorkerInterrupted, _cleanup_sc2


# A real child process exercises supervision and receipts without importing game code.
CHILD = r"""
from pathlib import Path
import hashlib,json,os,sys,time
from scv_star.data.bc_isolated import _identity
task_path,result_path=map(Path,sys.argv[1:])
payload=task_path.read_bytes(); task=json.loads(payload)
mode=task['fixture_mode']
if mode=='crash':os._exit(37)
if mode=='missing':sys.exit(0)
if mode=='timeout':time.sleep(30)
if mode=='slow':time.sleep(0.4)
result={'status':'failed','sha256':task['row']['sha256'],'build':task['row']['identity']['base_build'],
        'error':'synthetic ordinary failure','child_environment':{k:os.environ.get(k) for k in
        ('PYTHONFAULTHANDLER','PYTHONUTF8','PYTHONUNBUFFERED')}}
if mode in ('accepted','accepted_bad_count'):
 unit=Path(task['unit']); unit.mkdir(parents=True,exist_ok=True)
 shard=unit/'synthetic-shard.gz';shard.write_bytes(b'Synthetic fixture, no SC2 data')
 manifest={'training_ready':True,'files':[{'sha256':task['row']['sha256'],'split':task['row']['bc_split']}],
           'shards':[{'path':shard.name,'sha256':hashlib.sha256(shard.read_bytes()).hexdigest()}],
           'rows':256,'engine_base_build':task['row']['identity']['base_build']}
 (unit/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
 from scv_star.data.bc_batch import completed_unit
 result=completed_unit(unit)
 if mode=='accepted_bad_count':result['rows']+=1
receipt={'task_sha256':hashlib.sha256(payload).hexdigest(),'task_identity':_identity(task),
         'pid':os.getpid(),'ppid':os.getppid(),'result':result}
if mode=='wrong_identity':receipt['task_identity']['sha256']='f'*64
result_path.write_text(json.dumps(receipt),encoding='utf8')
"""


class IsolatedExecutorTests(unittest.TestCase):
    def task(self, root, digit="a", mode="ordinary"):
        return {
            "root": str(root),
            "unit": str(root / "data/processed/isolated" / (digit * 64)),
            "row": {
                "sha256": digit * 64,
                "bc_split": "train",
                "identity": {
                    "game_version": "5.0.8.86383",
                    "base_build": 86383,
                    "data_build": 86383,
                    "data_version": "fixture-data-version",
                },
                "screening_status": "eligible",
                "human_1v1": True,
                "outcome_label_ready": True,
            },
            "engine": {},
            "fixture_mode": mode,
        }

    def command(self, task, result):
        return [sys.executable, "-B", "-c", CHILD, str(task), str(result)]

    def test_real_abrupt_exit_does_not_break_other_workers_or_later_submissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original_environment = dict(os.environ)
            with (
                patch("scv_star.data.bc_isolated._worker_command", side_effect=self.command),
                IsolatedExecutor(2, root / "artifacts/attempts", root) as executor,
            ):
                crashed = executor.submit(self.task(root, "a", "crash"))
                healthy = executor.submit(self.task(root, "b", "slow"))
                with self.assertRaises(WorkerInterrupted) as raised:
                    crashed.result(timeout=20)
                self.assertEqual(raised.exception.exitcode, 37)
                evidence = json.loads((raised.exception.attempt_dir / "execution.json").read_text())
                self.assertEqual(evidence["status"], "infra_interrupted")
                self.assertEqual(evidence["exitcode"], 37)
                self.assertEqual(healthy.result(timeout=20)["status"], "failed")
                later = executor.submit(self.task(root, "c"))
                result = later.result(timeout=20)
                self.assertEqual(
                    result["child_environment"],
                    {"PYTHONFAULTHANDLER": "1", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
                )
            self.assertEqual(dict(os.environ), original_environment)
            attempts = list((root / "artifacts/attempts").iterdir())
            self.assertEqual(len(attempts), 3)
            pids = [json.loads((p / "execution.json").read_text())["pid"] for p in attempts]
            self.assertEqual(len(set(pids)), 3)

    def test_zero_exit_requires_receipt_and_matching_task_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch("scv_star.data.bc_isolated._worker_command", side_effect=self.command),
                IsolatedExecutor(1, root / "artifacts/attempts", root) as executor,
            ):
                for digit, mode in (("a", "missing"), ("b", "wrong_identity")):
                    with self.subTest(mode=mode), self.assertRaises(WorkerInterrupted) as raised:
                        executor.submit(self.task(root, digit, mode)).result(timeout=20)
                    self.assertEqual(raised.exception.exitcode, 0)
                    self.assertIn("receipt", raised.exception.reason)

    def test_accepted_receipt_must_match_the_frozen_completed_unit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch("scv_star.data.bc_isolated._worker_command", side_effect=self.command),
                IsolatedExecutor(1, root / "artifacts/attempts", root) as executor,
            ):
                good = executor.submit(self.task(root, "a", "accepted")).result(timeout=20)
                self.assertEqual(good["status"], "accepted")
                with self.assertRaisesRegex(WorkerInterrupted, "frozen completed unit"):
                    executor.submit(self.task(root, "b", "accepted_bad_count")).result(timeout=20)

    def test_timeout_is_an_interruption_and_duplicate_units_are_not_started(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch("scv_star.data.bc_isolated._worker_command", side_effect=self.command),
                IsolatedExecutor(
                    1, root / "artifacts/attempts", root, timeout_seconds=0.2
                ) as executor,
            ):
                task = self.task(root, "a", "timeout")
                future = executor.submit(task)
                with self.assertRaisesRegex(ValueError, "already submitted"):
                    executor.submit(task)
                with self.assertRaisesRegex(WorkerInterrupted, "deadline"):
                    future.result(timeout=20)
            self.assertEqual(len(list((root / "artifacts/attempts").iterdir())), 1)

    def test_real_worker_cli_reports_ordinary_exception_without_starting_game(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # Missing provenance fails at the very start of run_candidate, before any Client.
            with IsolatedExecutor(1, root / "artifacts/attempts", root) as executor:
                result = executor.submit(self.task(root)).result(timeout=20)
            self.assertEqual(result["status"], "failed")
            self.assertIn("provenance", result["error"])

    def test_sc2_cleanup_matches_only_this_units_exact_temp_dirs(self):
        with tempfile.TemporaryDirectory() as temp:
            unit = Path(temp).resolve() / "data/processed/unit"
            owned, unrelated, prefix_neighbour = MagicMock(), MagicMock(), MagicMock()
            for process, pid, directory in (
                (owned, 101, unit / "replay/client/temp"),
                (unrelated, 102, unit.parent / "other/replay/client/temp"),
                (prefix_neighbour, 103, unit.parent / "unit-extra/replay/client/temp"),
            ):
                process.pid = pid
                process.info = {
                    "pid": pid,
                    "name": "SC2_x64.exe",
                    "create_time": 123,
                    "cmdline": ["SC2_x64.exe", "-tempDir", str(directory)],
                }
                process.create_time.return_value = 123
            with (
                patch(
                    "scv_star.data.bc_isolated.psutil.process_iter",
                    return_value=[owned, unrelated, prefix_neighbour],
                ),
                patch("scv_star.data.bc_isolated.psutil.wait_procs", return_value=([owned], [])),
            ):
                evidence = _cleanup_sc2(unit)
            owned.terminate.assert_called_once()
            unrelated.terminate.assert_not_called()
            prefix_neighbour.terminate.assert_not_called()
            self.assertEqual(evidence[0]["pid"], 101)
            self.assertEqual(evidence[0]["status"], "owned_orphan_exited")


if __name__ == "__main__":
    unittest.main()
