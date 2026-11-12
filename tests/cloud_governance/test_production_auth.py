"""Production auth guards for cloud connections."""

from __future__ import annotations

import pytest

from rai_gateway.cloud_governance.domain import AuthMode
from rai_gateway.cloud_governance.service import CloudGovernanceService
from rai_gateway.core.config import settings


def test_customer_adc_rejected_in_production(db_session, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    svc = CloudGovernanceService(db_session)
    with pytest.raises(ValueError, match="customer_adc"):
        svc.create_connection(
            provider="gcp",
            display_name="Prod",
            scope_identifier="proj",
            auth_mode=AuthMode.CUSTOMER_ADC.value,
            auth_config={},
            enforcement_enabled=False,
            created_by="admin",
        )
