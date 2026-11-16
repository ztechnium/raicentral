import pytest

from rai_gateway.finops.published_pricing import estimate_cost_usd, published_entry


def test_gemini_flash_lite_cost_matches_published_prices() -> None:
    # 12 in * $0.25/1M + 29 out * $1.50/1M
    cost = estimate_cost_usd("vertex", "gemini-3.1-flash-lite", input_tokens=12, output_tokens=29)
    assert cost == pytest.approx(12 * 0.25e-6 + 29 * 1.5e-6)


def test_cached_tokens_use_cached_rate() -> None:
    cost = estimate_cost_usd(
        "google_ai", "gemini-3.1-flash-lite",
        input_tokens=1000, output_tokens=0, cached_input_tokens=400,
    )
    assert cost == pytest.approx(600 * 0.25e-6 + 400 * 0.025e-6)


def test_unknown_model_has_no_price() -> None:
    assert estimate_cost_usd("google", "no-such-model", input_tokens=5) is None
    assert published_entry("openai", "nope") is None


def test_provider_aliases_and_publisher_path_resolve() -> None:
    entry = published_entry("vertex", "publishers/google/models/gemini-3.1-flash-lite")
    assert entry is not None and entry.provider == "google"
    assert entry.pricing_unit == 1_000_000
