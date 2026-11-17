"""Built-in published list prices for hosted model providers (USD per 1M tokens).

This is the fallback used when no admin-owned row exists in the DB pricing catalog, so a
provider-reported token count can always be turned into an estimated USD cost. Rows in
``finops_pricing_catalog`` take precedence. To add a model, add one row to ``_PUBLISHED``
with the provider page it came from; nothing else needs to change.

Provider names use the FinOps-normalised form (``vertex``/``gemini`` -> ``google``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid5

from rai_gateway.finops.domain import normalize_provider
from rai_gateway.models.finops import PricingCatalogEntry

PRICING_UNIT = 1_000_000  # prices below are per 1M tokens
SEED_ACTOR = "published-pricing"
_NAMESPACE = UUID("5b6f0b1e-2c1d-4e47-9a3c-7d4f8e9a1b20")
_EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class PublishedPrice:
    provider: str
    model: str
    input: float  # USD / 1M input tokens (text, image, video)
    output: float  # USD / 1M output tokens, including thinking tokens
    cached_input: float | None = None  # USD / 1M cached input tokens
    source_url: str = ""
    verified_on: str = ""
    notes: str = ""


_PUBLISHED: tuple[PublishedPrice, ...] = (
    PublishedPrice(
        provider="google",
        model="gemini-3.1-flash-lite",
        input=0.25,
        output=1.50,
        cached_input=0.025,
        source_url="https://ai.google.dev/gemini-api/docs/pricing",
        verified_on="2026-11-16",
        notes="Standard tier, text/image/video input. Audio input is $0.50/1M and is "
        "not modelled here (token usage is not split by modality).",
    ),
)

_INDEX: dict[tuple[str, str], PublishedPrice] = {
    (p.provider, p.model): p for p in _PUBLISHED
}


def _lookup(provider: str | None, model: str | None) -> PublishedPrice | None:
    key = normalize_provider(provider)
    if not key or not model:
        return None
    name = model.strip()
    # Accept "publishers/google/models/<id>" style names.
    name = name.rsplit("/", 1)[-1]
    return _INDEX.get((key, name))


def published_entry(provider: str | None, model: str | None) -> PricingCatalogEntry | None:
    """Detached catalog row for a published price, or None when the model is unknown."""
    price = _lookup(provider, model)
    if price is None:
        return None
    return PricingCatalogEntry(
        id=uuid5(_NAMESPACE, f"{price.provider}:{price.model}"),
        provider=price.provider,
        model=price.model,
        input_token_price=price.input,
        output_token_price=price.output,
        cached_input_token_price=price.cached_input,
        pricing_unit=PRICING_UNIT,
        currency="USD",
        pricing_metadata={"notes": price.notes, "kind": "published_list_price"},
        source_url=price.source_url or None,
        verified_at=_EFFECTIVE_FROM,
        effective_from=_EFFECTIVE_FROM,
        enabled=True,
        created_by=SEED_ACTOR,
    )


def estimate_cost_usd(
    provider: str | None,
    model: str | None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> float | None:
    """General helper: USD for a token usage using published list prices.

    ``input_tokens`` is the total prompt size; cached tokens are billed at the cached
    rate and the rest at the normal input rate. Returns None for unknown models.
    """
    price = _lookup(provider, model)
    if price is None:
        return None
    cached = min(max(cached_input_tokens, 0), max(input_tokens, 0))
    fresh = max(input_tokens, 0) - cached
    cached_rate = price.cached_input if price.cached_input is not None else price.input
    return (
        fresh * price.input + cached * cached_rate + max(output_tokens, 0) * price.output
    ) / PRICING_UNIT
