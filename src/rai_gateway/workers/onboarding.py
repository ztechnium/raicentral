"""Onboarding worker: prepares saved intake sources (Step 02).

    rai-worker onboarding --once        one bounded pass, then exit
    rai-worker onboarding --interval 2  repeat bounded passes until SIGTERM
    rai-worker onboarding --healthcheck exit 0 only while the loop is alive

The database row is the durable queue; unfinished claims are recovered through
leases, so terminating this process never loses work.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import tempfile
import time
import uuid
from pathlib import Path

from rai_gateway.core.database import SessionLocal
from rai_gateway.onboarding.preparation import run_pass
from rai_gateway.onboarding.storage import SourceStorageError, get_source_store

HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "rai-onboarding-worker.heartbeat"
HEARTBEAT_MAX_AGE_SECONDS = 120.0
CLEANUP_EVERY_SECONDS = 600.0


def _touch_heartbeat() -> None:
    try:
        HEARTBEAT_FILE.write_text(str(time.time()))
    except OSError:
        pass


def healthcheck() -> int:
    try:
        age = time.time() - float(HEARTBEAT_FILE.read_text())
    except (OSError, ValueError):
        return 1
    return 0 if age <= HEARTBEAT_MAX_AGE_SECONDS else 1


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RaiCentral onboarding worker")
    parser.add_argument("--once", action="store_true", help="Run one bounded pass")
    parser.add_argument(
        "--interval", type=float, default=2.0, help="Loop interval seconds"
    )
    parser.add_argument(
        "--healthcheck", action="store_true", help="Container health probe"
    )
    args = parser.parse_args(argv)
    if args.healthcheck:
        return healthcheck()

    store = get_source_store()
    try:
        store.check_writable()
    except SourceStorageError as exc:
        print(f"[onboarding] source storage unavailable: {exc}")
        return 2

    worker_id = _worker_id()
    if args.once:
        print(run_pass(SessionLocal, store, worker_id=worker_id, cleanup=True))
        return 0

    stop = {"flag": False}

    def _request_stop(*_: object) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    last_cleanup = 0.0
    while not stop["flag"]:
        _touch_heartbeat()
        do_cleanup = time.monotonic() - last_cleanup >= CLEANUP_EVERY_SECONDS
        try:
            stats = run_pass(
                SessionLocal, store, worker_id=worker_id, cleanup=do_cleanup
            )
        except Exception as exc:  # noqa: BLE001 - keep serving; leases recover work
            print(f"[onboarding] pass failed: {type(exc).__name__}")
            stats = {}
        if do_cleanup:
            last_cleanup = time.monotonic()
        if any(stats.get(k) for k in ("processed", "reaped", "cleaned")):
            print(f"[onboarding] {stats}")
        # Sleep in short slices so SIGTERM is honoured promptly.
        waited = 0.0
        while waited < args.interval and not stop["flag"]:
            time.sleep(min(0.25, args.interval - waited))
            waited += 0.25
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
