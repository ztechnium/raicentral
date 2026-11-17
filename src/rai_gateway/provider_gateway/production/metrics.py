"""Gateway SLO metrics (in-process; export via doctor / structured logs)."""

from __future__ import annotations

import statistics
import threading
from collections import deque
from dataclasses import dataclass, field

from rai_gateway.core.observability import logger


@dataclass
class GatewayMetricSnapshot:
    samples: int = 0
    gateway_overhead_ms: dict[str, float | None] = field(default_factory=dict)
    policy_decision_ms: dict[str, float | None] = field(default_factory=dict)
    provider_latency_ms: dict[str, float | None] = field(default_factory=dict)
    time_to_first_token_ms: dict[str, float | None] = field(default_factory=dict)
    error_rate: float = 0.0
    stream_interruption_rate: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "samples": self.samples,
            "gateway_overhead_ms": self.gateway_overhead_ms,
            "policy_decision_ms": self.policy_decision_ms,
            "provider_latency_ms": self.provider_latency_ms,
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "error_rate": self.error_rate,
            "stream_interruption_rate": self.stream_interruption_rate,
        }


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[min(max(idx, 0), len(ordered) - 1)]


class GatewayMetrics:
    def __init__(self, *, max_samples: int = 2000) -> None:
        self._lock = threading.Lock()
        self._max = max_samples
        self._gateway_ms: deque[float] = deque(maxlen=max_samples)
        self._policy_ms: deque[float] = deque(maxlen=max_samples)
        self._provider_ms: deque[float] = deque(maxlen=max_samples)
        self._ttft_ms: deque[float] = deque(maxlen=max_samples)
        self._total = 0
        self._errors = 0
        self._stream_total = 0
        self._stream_interrupted = 0

    def record_request(
        self,
        *,
        gateway_overhead_ms: int | float | None = None,
        policy_decision_ms: int | float | None = None,
        provider_latency_ms: int | float | None = None,
        time_to_first_token_ms: int | float | None = None,
        error: bool = False,
        streamed: bool = False,
        stream_interrupted: bool = False,
    ) -> None:
        with self._lock:
            self._total += 1
            if error:
                self._errors += 1
            if streamed:
                self._stream_total += 1
                if stream_interrupted:
                    self._stream_interrupted += 1
            if gateway_overhead_ms is not None:
                self._gateway_ms.append(float(gateway_overhead_ms))
            if policy_decision_ms is not None:
                self._policy_ms.append(float(policy_decision_ms))
            if provider_latency_ms is not None:
                self._provider_ms.append(float(provider_latency_ms))
            if time_to_first_token_ms is not None:
                self._ttft_ms.append(float(time_to_first_token_ms))

    def snapshot(self) -> GatewayMetricSnapshot:
        with self._lock:
            gw = list(self._gateway_ms)
            pol = list(self._policy_ms)
            prov = list(self._provider_ms)
            ttft = list(self._ttft_ms)
            total = self._total
            errors = self._errors
            st = self._stream_total
            si = self._stream_interrupted
        return GatewayMetricSnapshot(
            samples=total,
            gateway_overhead_ms={
                "p50": _percentile(gw, 50),
                "p95": _percentile(gw, 95),
                "p99": _percentile(gw, 99),
                "mean": statistics.fmean(gw) if gw else None,
            },
            policy_decision_ms={
                "p50": _percentile(pol, 50),
                "p95": _percentile(pol, 95),
                "p99": _percentile(pol, 99),
            },
            provider_latency_ms={
                "p50": _percentile(prov, 50),
                "p95": _percentile(prov, 95),
                "p99": _percentile(prov, 99),
            },
            time_to_first_token_ms={
                "p50": _percentile(ttft, 50),
                "p95": _percentile(ttft, 95),
                "p99": _percentile(ttft, 99),
            },
            error_rate=(errors / total) if total else 0.0,
            stream_interruption_rate=(si / st) if st else 0.0,
        )

    def emit_periodic_summary(self) -> None:
        snap = self.snapshot()
        logger.info("gateway_slo_snapshot", **snap.to_dict())


gateway_metrics = GatewayMetrics()
