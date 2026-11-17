"""Unit tests for drift diff helper."""

from __future__ import annotations

from rai_gateway.cloud_governance.drift import _diff_model_armor


def test_no_baseline_no_drift():
    assert (
        _diff_model_armor({"note": "no_applied_baseline"}, {"templates": []}) is False
    )


def test_detects_template_drift():
    assert _diff_model_armor(
        {"templates": [{"name": "a"}]},
        {"templates": [{"name": "b"}]},
    )
