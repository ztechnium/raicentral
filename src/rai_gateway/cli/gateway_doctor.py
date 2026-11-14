"""``rai gateway doctor`` — production readiness checks for the enforcement gateway."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import click
import httpx

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.production.fail_closed import FailClosedMatrix
from rai_gateway.provider_gateway.production.policy_cache import shared_policy_cache
from rai_gateway.provider_gateway.production.rollout import resolve_rollout_phase
from rai_gateway.runtime_enforcement.gateway_action import GatewayEnforcementMode


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def run_doctor(
    *,
    base_url: str,
    runtime_secret: str | None,
    skip_stream: bool,
) -> tuple[list[dict[str, Any]], bool]:
    results: list[dict[str, Any]] = []
    all_ok = True

    try:
        r = httpx.get(f"{base_url.rstrip('/')}/health", timeout=5.0)
        ok = r.status_code == 200
        results.append(_check("gateway_reachable", ok, f"HTTP {r.status_code}"))
        all_ok = all_ok and ok
    except Exception as exc:
        results.append(_check("gateway_reachable", False, str(exc)))
        all_ok = False
        return results, all_ok

    try:
        r = httpx.get(f"{base_url.rstrip('/')}/ready", timeout=5.0)
        body = r.json()
        ok = r.status_code == 200 and body.get("status") == "ready"
        results.append(
            _check(
                "readiness",
                ok,
                f"status={body.get('status')} checks={body.get('checks')}",
            )
        )
        all_ok = all_ok and ok
    except Exception as exc:
        results.append(_check("readiness", False, str(exc)))
        all_ok = False

    cache_stats = shared_policy_cache.stats()
    results.append(
        _check(
            "policy_cache_local",
            cache_stats.healthy(),
            json.dumps(cache_stats.snapshot()),
        )
    )

    matrix = FailClosedMatrix(mode=settings.gateway_enforcement_mode)
    enforce = matrix.mode == GatewayEnforcementMode.ENFORCE
    results.append(
        _check(
            "fail_closed_enforce",
            enforce or settings.fail_closed_on_rai_unavailable,
            f"mode={matrix.mode.value} fail_closed={settings.fail_closed_on_rai_unavailable}",
        )
    )

    results.append(
        _check(
            "rollout_phase",
            True,
            resolve_rollout_phase().value,
        )
    )

    if runtime_secret:
        try:
            r = httpx.get(
                f"{base_url.rstrip('/')}/v1/providers/_gateway/status",
                headers={"X-RAI-Agent-Key": runtime_secret},
                timeout=10.0,
            )
            ok = r.status_code == 200
            results.append(
                _check(
                    "runtime_identity_valid",
                    ok,
                    "gateway status" if ok else f"HTTP {r.status_code}",
                )
            )
            all_ok = all_ok and ok
        except Exception as exc:
            results.append(_check("runtime_identity_valid", False, str(exc)))
            all_ok = False
    else:
        results.append(
            _check(
                "runtime_identity_valid",
                False,
                "skipped — pass --runtime-secret or RAI_RUNTIME_SECRET",
            )
        )

    if not skip_stream:
        results.append(
            _check(
                "streaming_probe",
                True,
                "skipped — use integration tests for streaming validation",
            )
        )

    return results, all_ok


@click.command("doctor")
@click.option("--base-url", default=None, help="Gateway base URL")
@click.option("--runtime-secret", default=None, help="Workload runtime credential")
@click.option("--skip-stream", is_flag=True, default=False)
@click.option("--json-out", is_flag=True, default=False)
def gateway_doctor(
    base_url: str | None,
    runtime_secret: str | None,
    skip_stream: bool,
    json_out: bool,
) -> None:
    """Validate production gateway readiness (Step 8)."""
    url = base_url or os.environ.get("RAI_GATEWAY_URL") or settings.app_public_url
    secret = runtime_secret or os.environ.get("RAI_RUNTIME_SECRET")
    results, all_ok = run_doctor(
        base_url=url, runtime_secret=secret, skip_stream=skip_stream
    )
    if json_out:
        click.echo(json.dumps({"ok": all_ok, "checks": results}, indent=2))
    else:
        for row in results:
            mark = "OK" if row["ok"] else "FAIL"
            click.echo(f"[{mark}] {row['name']}: {row['detail']}")
    if not all_ok:
        sys.exit(1)
