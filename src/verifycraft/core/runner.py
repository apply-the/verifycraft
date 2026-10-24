"""Check runners for VerifyCraft.

Includes the abstract CheckRunner interface, a deterministic FakeRunner for
offline/unit testing, and a TrustedPytestRunner for controlled local execution.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import IO, Any
from uuid import uuid4

from verifycraft.contracts.models import (
    CheckSpec,
    Evidence,
    ExecutionPolicy,
    ResourceUsage,
)
from verifycraft.core.policy import sanitize_environment


class ExecutionTimeoutError(Exception):
    """Raised when check execution exceeds policy timeout."""


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Terminate the process and all child processes in its process group."""
    if proc.poll() is not None:
        return
    try:
        if hasattr(os, "killpg") and hasattr(os, "getpgid"):
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
                proc.wait(timeout=1.0)
                return
            except (ProcessLookupError, PermissionError):
                pass
        proc.kill()
        proc.wait(timeout=1.0)
    except Exception:
        pass


class CheckRunner(ABC):
    """Abstract interface for check execution backends."""

    @abstractmethod
    def run(
        self,
        spec: CheckSpec,
        repo_path: str,
        policy: ExecutionPolicy,
        fingerprint: str,
    ) -> Evidence:
        """Execute the check spec under the given policy and return structured evidence."""


class FakeRunner(CheckRunner):
    """Deterministic fake runner for unit, contract, and offline benchmark tests.

    Never executes subprocesses. Allows pre-registering responses or providing
    deterministic fallbacks.
    """

    def __init__(
        self,
        responses: Mapping[str, tuple[int, str, str]] | None = None,
        default_response: tuple[int, str, str] = (0, "fake check passed", ""),
    ) -> None:
        self.responses = dict(responses or {})
        self.default_response = default_response
        self.invocations: list[CheckSpec] = []

    def set_response(self, check_id: str, exit_code: int, stdout: str, stderr: str = "") -> None:
        self.responses[check_id] = (exit_code, stdout, stderr)

    def run(
        self,
        spec: CheckSpec,
        repo_path: str,
        policy: ExecutionPolicy,
        fingerprint: str,
    ) -> Evidence:
        self.invocations.append(spec)
        exit_code, stdout, stderr = self.responses.get(spec.check_id, self.default_response)

        raw_bytes = (stdout + stderr).encode("utf-8")
        truncated = False
        if len(raw_bytes) > policy.max_output_bytes:
            stdout = stdout[: policy.max_output_bytes]
            truncated = True

        return Evidence(
            evidence_id=f"evi-{uuid4().hex[:8]}",
            check_id=spec.check_id,
            evidence_type="fake_execution",
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            output_truncated=truncated,
            fingerprint=fingerprint,
            resource_usage=ResourceUsage(
                wall_time_seconds=0.01,
                attempts=1,
                output_bytes=len(raw_bytes),
            ),
        )


def _handle_spawn_error(
    exc: OSError,
    spec: CheckSpec,
    fingerprint: str,
    start_time: float,
) -> Evidence:
    """Handle process spawn errors and return corresponding Evidence."""
    wall_time = time.perf_counter() - start_time
    if isinstance(exc, FileNotFoundError):
        exit_code = 127
        msg = f"Execution failed: command or binary not found: {exc}"
    elif isinstance(exc, PermissionError):
        exit_code = 126
        msg = f"Execution failed: permission denied: {exc}"
    else:
        exit_code = 1
        msg = f"Execution failed: OS error: {exc}"

    return Evidence(
        evidence_id=f"evi-{uuid4().hex[:8]}",
        check_id=spec.check_id,
        evidence_type="pytest_execution",
        exit_code=exit_code,
        stdout="",
        stderr=msg,
        output_truncated=False,
        fingerprint=fingerprint,
        resource_usage=ResourceUsage(wall_time_seconds=round(wall_time, 4)),
    )


class _StreamCollector:
    """Thread-safe collector for stdout and stderr streams with byte caps."""

    def __init__(self, max_output_bytes: int) -> None:
        self.max_output_bytes = max_output_bytes
        self.stdout_chunks: list[bytes] = []
        self.stderr_chunks: list[bytes] = []
        self.collected_bytes = 0
        self.truncated = False
        self._lock = threading.Lock()

    def read_stream(self, stream: IO[bytes] | None, chunks: list[bytes]) -> None:
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                with self._lock:
                    if self.collected_bytes < self.max_output_bytes:
                        space_left = self.max_output_bytes - self.collected_bytes
                        accepted = chunk[:space_left]
                        chunks.append(accepted)
                        self.collected_bytes += len(accepted)
                        if len(chunk) > space_left:
                            self.truncated = True
                    else:
                        self.truncated = True
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    def start_collection(
        self, proc: subprocess.Popen[bytes]
    ) -> tuple[threading.Thread, threading.Thread]:
        t_stdout = threading.Thread(target=self.read_stream, args=(proc.stdout, self.stdout_chunks))
        t_stderr = threading.Thread(target=self.read_stream, args=(proc.stderr, self.stderr_chunks))
        t_stdout.daemon = True
        t_stderr.daemon = True
        t_stdout.start()
        t_stderr.start()
        return t_stdout, t_stderr


def _wait_for_process(
    proc: subprocess.Popen[bytes],
    timeout_seconds: float,
) -> bool:
    """Poll process until completion or timeout deadline, terminating process group if expired."""
    deadline = time.perf_counter() + timeout_seconds
    while proc.poll() is None:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            _terminate_process_group(proc)
            return True
        time.sleep(min(0.02, remaining))
    return False


class TrustedPytestRunner(CheckRunner):
    """Subprocess-based pytest runner for trusted local execution.

    SAFETY INVARIANTS:
    - Never uses shell=True.
    - Requires explicit opt-in policy mode (TRUSTED_LOCAL).
    - Enforces wall-clock execution timeouts and terminates the entire process group.
    - Enforces maximum output byte caps in real-time during execution.
    - Sanitizes environment variables to prevent secret leakage.
    - Captures and reports runner exceptions without crashing.
    """

    def run(
        self,
        spec: CheckSpec,
        repo_path: str,
        policy: ExecutionPolicy,
        fingerprint: str,
    ) -> Evidence:
        env = sanitize_environment(policy, overrides=spec.env_overrides)
        command = [sys.executable, "-m", "pytest", spec.target_path, *spec.args]

        start_time = time.perf_counter()

        popen_kwargs: dict[str, Any] = {
            "cwd": repo_path,
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
        }
        if hasattr(os, "setsid"):
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(command, **popen_kwargs)
        except OSError as exc:
            return _handle_spawn_error(exc, spec, fingerprint, start_time)

        collector = _StreamCollector(max_output_bytes=policy.max_output_bytes)
        t_stdout, t_stderr = collector.start_collection(proc)

        timed_out = _wait_for_process(proc, policy.timeout_seconds)
        wall_time = time.perf_counter() - start_time
        t_stdout.join(timeout=2.0)
        t_stderr.join(timeout=2.0)

        if timed_out:
            exit_code = 124
            stderr_extra = b"\nCommand timed out."
        else:
            exit_code = proc.returncode if proc.returncode is not None else 1
            stderr_extra = b""

        stdout = b"".join(collector.stdout_chunks).decode("utf-8", errors="replace")
        stderr = (b"".join(collector.stderr_chunks) + stderr_extra).decode(
            "utf-8", errors="replace"
        )

        return Evidence(
            evidence_id=f"evi-{uuid4().hex[:8]}",
            check_id=spec.check_id,
            evidence_type="pytest_execution",
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            output_truncated=collector.truncated,
            fingerprint=fingerprint,
            resource_usage=ResourceUsage(
                wall_time_seconds=round(wall_time, 4),
                attempts=1,
                output_bytes=collector.collected_bytes,
            ),
        )
