"""Metering idempotency — retries must not double-settle."""

from __future__ import annotations

from rai_gateway.finops.domain import CostState, MeteringSource
from rai_gateway.finops.metering import RuntimeMeteringService
from rai_gateway.finops.service import FinOpsService
from rai_gateway.models.finops import UsageSettlement


def test_sdk_retry_same_external_span_no_duplicate_settlement(db_session):
    FinOpsService(db_session).upsert_pricing(
        provider="openai",
        model="gpt-idem",
        input_token_price=1.0,
        output_token_price=1.0,
        created_by="tester",
    )
    metering = RuntimeMeteringService(db_session)
    first = metering.record_model_usage(
        external_span_id="exec-1",
        provider="openai",
        model="gpt-idem",
        usage={"input_tokens": 1000, "output_tokens": 0, "source": "PROVIDER_RESPONSE"},
    )
    second = metering.record_model_usage(
        external_span_id="exec-1",
        provider="openai",
        model="gpt-idem",
        usage={"input_tokens": 1000, "output_tokens": 0, "source": "PROVIDER_RESPONSE"},
    )
    db_session.commit()
    assert first.id == second.id
    assert second.cost_state == CostState.METERED.value
    settlements = (
        db_session.query(UsageSettlement)
        .filter(UsageSettlement.metering_span_id == first.id)
        .all()
    )
    assert len(settlements) == 1
    assert (
        settlements[0].metering_source == MeteringSource.PRICING_CATALOG_DERIVED.value
    )


def test_span_for_already_settled_receipt_adopts_settlement(db_session):
    """Gateway path: settle() runs first, then the compat span; no second settlement."""
    from uuid import uuid4

    receipt_id = uuid4()
    usage = {
        "provider": "vertex",
        "model": "gemini-3.1-flash-lite",
        "input_tokens": 12,
        "output_tokens": 29,
        "total_tokens": 41,
        "source": "PROVIDER_RESPONSE",
    }
    settlement = FinOpsService(db_session).settle(
        usage=usage, enforcement_receipt_id=receipt_id
    )
    span = RuntimeMeteringService(db_session).record_span(
        enforcement_receipt_id=receipt_id,
        external_span_id=f"receipt:{receipt_id}",
        provider="vertex",
        model="gemini-3.1-flash-lite",
        usage=usage,
    )
    settlement.metering_span_id = span.id
    db_session.commit()
    rows = (
        db_session.query(UsageSettlement)
        .filter(UsageSettlement.enforcement_receipt_id == receipt_id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].metering_span_id == span.id
    assert span.total_tokens == 41
    assert span.cost_state != CostState.UNAVAILABLE.value
