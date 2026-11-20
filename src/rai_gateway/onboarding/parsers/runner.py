"""Parent-side supervision of the killable parser child process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .contracts import FAILED, Limits, result


class ParserStopped(Exception):
    """The parent asked the child to stop (cancellation or lost ownership)."""


def _child_env() -> dict[str, str]:
    # Deliberately minimal: no provider keys, no database URL, no proxies.
    src_root = str(Path(__file__).resolve().parents[3])
    env = {
        "PYTHONPATH": src_root,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for keep in ("SYSTEMROOT", "PATH", "TMPDIR", "TEMP", "TMP"):
        if os.environ.get(keep) and (keep != "PATH" or os.name == "nt"):
            env[keep] = os.environ[keep]
    return env


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)  # always reap
    except subprocess.TimeoutExpired:  # pragma: no cover
        pass


def run_parser(
    path: str,
    family: str,
    kind: str,
    options: dict[str, Any],
    limits: Limits,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    poll_seconds: float = 0.25,
) -> dict[str, Any]:
    """Run one parse in a child; terminate and reap it on timeout/limit/stop."""
    job = json.dumps(
        {
            "path": path,
            "family": family,
            "kind": kind,
            "options": options,
            "limits": limits.to_dict(),
        }
    ).encode("utf-8")
    output_cap = limits.output_bytes * 2 + 1024 * 1024
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(
            [sys.executable, "-s", "-m", "rai_gateway.onboarding.parsers.child"],
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=subprocess.DEVNULL,
            env=_child_env(),
            close_fds=True,
        )
        try:
            assert proc.stdin is not None
            proc.stdin.write(job)
            proc.stdin.close()
        except OSError:
            pass
        started = time.monotonic()
        try:
            while True:
                remaining = limits.wall_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    _kill(proc)
                    return result(FAILED, reason="PARSER_TIMEOUT")
                try:
                    proc.wait(timeout=max(0.001, min(poll_seconds, remaining)))
                    break
                except subprocess.TimeoutExpired:
                    pass
                if should_stop():
                    _kill(proc)
                    raise ParserStopped()
                if time.monotonic() - started > limits.wall_seconds:
                    _kill(proc)
                    return result(FAILED, reason="PARSER_TIMEOUT")
                if (
                    out.tell() > output_cap
                    or os.fstat(out.fileno()).st_size > output_cap
                ):
                    _kill(proc)
                    return result(FAILED, reason="OUTPUT_LIMIT")
        except BaseException:
            if proc.poll() is None:
                _kill(proc)
            raise
        out.seek(0)
        raw = out.read(output_cap + 1)
    if not raw or len(raw) > output_cap:
        return result(FAILED, reason="PARSER_CRASHED")
    try:
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict) or "outcome" not in parsed:
            raise ValueError
    except ValueError:
        return result(FAILED, reason="PARSER_CRASHED")
    return parsed
