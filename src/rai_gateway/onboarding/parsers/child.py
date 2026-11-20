"""Sandboxed parser child: ``python -m rai_gateway.onboarding.parsers.child``.

Reads one JSON job from stdin, writes one JSON result to stdout. It imports only
the pure parsers, never settings or database code, and runs with a minimal
environment, so it holds no provider secrets and has no tool capabilities.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from . import parse_file
from .contracts import FAILED, PARTIAL, Limits, result


def apply_limits(limits: Limits) -> dict[str, bool]:
    """Apply OS limits where the platform supports them and report what is active."""
    active = {"memory": False, "cpu": False}
    try:
        import resource  # POSIX only
    except ImportError:
        return active
    try:
        resource.setrlimit(
            resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes)
        )
        active["memory"] = True
    except (ValueError, OSError):
        pass
    try:
        cpu = int(limits.wall_seconds) + 5
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        active["cpu"] = True
    except (ValueError, OSError):
        pass
    return active


def fit_output(res: dict[str, Any], budget: int) -> dict[str, Any]:
    """Never silently truncate: drop trailing blocks, record a gap, mark PARTIAL."""
    used = 0
    kept = []
    for blk in res["blocks"]:
        size = len(blk["text"].encode("utf-8")) + len(json.dumps(blk["locator"])) + 200
        if blk.get("cell_data"):
            size += len(json.dumps(blk["cell_data"], ensure_ascii=False))
        if used + size > budget:
            res["coverage"]["gaps"].append(
                {"code": "OUTPUT_LIMIT", "detail": "output budget"}
            )
            if res["outcome"] == "COMPLETE":
                res["outcome"] = PARTIAL
                res["reason"] = "OUTPUT_LIMIT"
            break
        used += size
        kept.append(blk)
    res["blocks"] = kept
    return res


def main() -> int:
    job = json.loads(sys.stdin.read())
    limits = Limits.from_dict(job["limits"])
    active = apply_limits(limits)
    try:
        res = parse_file(
            job["path"], job["family"], job["kind"], job["options"], limits
        )
        res = fit_output(res, limits.output_bytes)
    except MemoryError:
        res = result(FAILED, reason="PARSER_MEMORY_LIMIT")
    except Exception:  # noqa: BLE001 - never leak parser internals or content
        res = result(FAILED, reason="PARSER_ERROR")
    res["limits_active"] = active
    sys.stdout.buffer.write(json.dumps(res, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
