"""Small synchronous SC2 transport for local interface acceptance tests."""

from __future__ import annotations

import hashlib
import socket
import subprocess
import time
from pathlib import Path

import websocket
from google.protobuf.json_format import MessageToDict
from s2clientprotocol import sc2api_pb2 as sc


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def message_dict(message) -> dict:
    return MessageToDict(message, preserving_proto_field_name=True)


def validate_version(actual: dict, expected: dict) -> None:
    """Require every identity field; discovery and pinned runs are separate."""
    for field in ("game_version", "base_build", "data_build", "data_version"):
        if expected.get(field) in (None, ""):
            raise ValueError(f"Missing pinned version field: {field}")
        if actual.get(field) != expected[field]:
            raise ValueError(
                f"Version mismatch for {field}: {actual.get(field)!r} != {expected[field]!r}"
            )


class Client:
    """Own one process and socket. Never terminate unrelated game processes."""

    def __init__(
        self, install: Path, build: int, output: Path, expected=None, visible=False, display_mode=0
    ):
        self.install = install.resolve()
        self.binary = self.install / "Versions" / f"Base{build}" / "SC2_x64.exe"
        if not self.binary.is_file():
            raise FileNotFoundError(self.binary)
        self.binary_hash = sha256(self.binary)
        self.expected = expected
        if expected:
            validate_version(expected, expected)
        if expected and self.binary_hash != expected.get("binary_sha256"):
            raise ValueError("Game binary SHA-256 mismatch")
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.process = None
        self.ws = None
        self.log = None
        self.request_id = 0
        self.latencies = []
        self.cleanup = {}
        self.visible = visible
        self.display_mode = display_mode

    def __enter__(self):
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            temporary = self.output / "temp"
            temporary.mkdir()
            self.command = [
                str(self.binary),
                "-listen",
                "127.0.0.1",
                "-port",
                str(port),
                "-dataDir",
                str(self.install) + "\\",
                "-tempDir",
                str(temporary) + "\\",
                "-displayMode",
                str(self.display_mode),
            ]
            if self.display_mode == 0:
                self.command += [
                    "-windowwidth",
                    "1024",
                    "-windowheight",
                    "768",
                    "-windowx",
                    "50",
                    "-windowy",
                    "50",
                ]
            if self.expected:
                self.command += ["-dataVersion", self.expected["data_version"]]
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 1 if self.visible else 0
            self.log = (self.output / "client.log").open("wb")
            self.process = subprocess.Popen(
                self.command,
                cwd=self.install / "Support64",
                stdout=self.log,
                stderr=subprocess.STDOUT,
                startupinfo=startup,
            )
            deadline = time.monotonic() + 90
            last_error = None
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f"SC2 exited with code {self.process.returncode}; see {self.output / 'client.log'}"
                    )
                try:
                    self.ws = websocket.create_connection(
                        f"ws://127.0.0.1:{port}/sc2api",
                        timeout=2,
                        suppress_origin=True,
                        http_no_proxy=["127.0.0.1"],
                    )
                    break
                except (OSError, websocket.WebSocketException) as error:
                    last_error = error
                    time.sleep(0.25)
            if self.ws is None:
                raise TimeoutError(f"SC2 API did not start: {last_error}")
            self.ws.settimeout(60)
            self.version = message_dict(self.call("ping", sc.RequestPing()))
            if self.expected:
                validate_version(self.version, self.expected)
            return self
        except BaseException:
            self.close()
            raise

    def call(self, name: str, payload):
        self.request_id += 1
        request = sc.Request(id=self.request_id)
        getattr(request, name).CopyFrom(payload)
        start = time.perf_counter()
        self.ws.send_binary(request.SerializeToString())
        raw_response = self.ws.recv()
        response = sc.Response()
        response.ParseFromString(raw_response)
        self.latencies.append({"request": name, "seconds": time.perf_counter() - start})
        if response.id != request.id:
            raise RuntimeError(f"Unexpected response id: {response.id} != {request.id}")
        if response.error:
            raise RuntimeError(f"{name}: {list(response.error)}")
        if not response.HasField(name):
            raise RuntimeError(f"Missing {name} response; status={response.status}")
        result = getattr(response, name)
        if "error" in result.DESCRIPTOR.fields_by_name and result.HasField("error"):
            raise RuntimeError(f"{name}: {message_dict(result)}")
        return result

    def observe(self):
        return self.call("observation", sc.RequestObservation(disable_fog=False))

    def close(self):
        if self.ws is not None:
            try:
                self.ws.send_binary(sc.Request(quit=sc.RequestQuit()).SerializeToString())
                self.ws.settimeout(5)
                self.ws.recv()
            except (OSError, websocket.WebSocketException):
                pass
            self.ws.close()
            self.ws = None
        if self.process is not None:
            try:
                self.process.wait(timeout=10)
                self.cleanup["forced_termination"] = False
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=10)
                self.cleanup["forced_termination"] = True
            self.cleanup.update(
                pid=self.process.pid,
                returncode=self.process.returncode,
                exited=self.process.poll() is not None,
            )
        if self.log is not None:
            self.log.close()

    def __exit__(self, *args):
        self.close()
