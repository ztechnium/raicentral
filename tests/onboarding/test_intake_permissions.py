"""Permission boundaries and content redaction for onboarding intake."""

from __future__ import annotations

import json

import pytest

from conftest import PDF, Intake
from rai_gateway.governance.domain import GovernanceRole
from rai_gateway.onboarding.service import SOURCES_PERMISSION

SECRET_TITLE = "Confidential: Project Nightingale acquisition"
SECRET_PROMPT = "Our acquisition target is ACME Corp"
SECRET_FILE = "nightingale-term-sheet.pdf"


@pytest.fixture()
def populated(api):
    session = api.create(SECRET_TITLE).json()
    prompt = api.text(session, SECRET_PROMPT).json()
    upload = api.upload(prompt["session"], SECRET_FILE, PDF).json()
    run = api.prepare(upload["session"]).json()
    return {
        "session": upload["session"],
        "prompt": prompt,
        "upload": upload,
        "run": run,
    }


def _reader(client, make_user):
    return Intake(client, make_user(GovernanceRole.AI_SYSTEM_OWNER.value))


def test_admin_permission_is_in_auth_context(client, admin):
    response = client.get("/v1/auth/context", headers=admin)
    assert response.status_code == 200, response.text
    assert SOURCES_PERMISSION in json.dumps(response.json())


def test_metadata_only_reader_sees_counts_and_generic_labels_only(
    client, make_user, populated
):
    reader = _reader(client, make_user)
    sid = populated["session"]["id"]
    listing = reader.client.get(reader.BASE, headers=reader.headers)
    assert listing.status_code == 200
    detail = reader.get(sid)
    runs = reader.client.get(f"{reader.BASE}/{sid}/runs", headers=reader.headers)
    run = reader.client.get(
        f"{reader.BASE}/{sid}/runs/{populated['run']['run']['id']}",
        headers=reader.headers,
    )
    for response in (listing, detail, runs, run):
        assert response.status_code == 200, response.text
        text = response.text
        for secret in (
            SECRET_TITLE,
            SECRET_PROMPT,
            SECRET_FILE,
            "Nightingale",
            "storage",
        ):
            assert secret not in text
        assert "manifest" not in text and "sha256" not in text
    view = detail.json()
    assert view["label"].startswith("Session ")
    assert "title" not in view
    assert view["source_count"] == 2
    assert [s["label"] for s in view["sources"]] == ["Source 1", "Source 2"]
    assert all("display_name" not in s["current_revision"] for s in view["sources"])
    assert view["allowed_actions"] == []
    assert all(s["current_revision"]["byte_length"] > 0 for s in view["sources"])


def test_metadata_only_reader_cannot_read_content_history_or_mutate(
    client, make_user, populated
):
    reader = _reader(client, make_user)
    sid = populated["session"]["id"]
    source = populated["upload"]["source_id"]
    revision = populated["upload"]["revision_id"]
    base = f"{reader.BASE}/{sid}/sources/{source}/revisions"
    assert reader.client.get(base, headers=reader.headers).status_code == 403
    assert (
        reader.client.get(
            f"{base}/{revision}/content", headers=reader.headers
        ).status_code
        == 403
    )
    assert reader.create().status_code == 403
    assert reader.text(populated["session"], "x").status_code == 403
    assert reader.prepare(populated["session"]).status_code == 403
    assert reader.cancel(sid, populated["run"]["run"]["id"]).status_code == 403


def test_sdk_manage_without_source_permission_cannot_mutate_intake(
    client, make_user, populated
):
    technical = Intake(client, make_user(GovernanceRole.TECHNICAL_OWNER.value))
    assert technical.create().status_code == 403
    assert technical.prepare(populated["session"]).status_code == 403
    # ...and still sees only sanitized metadata.
    detail = technical.get(populated["session"]["id"])
    assert detail.status_code == 200 and SECRET_TITLE not in detail.text
    history = technical.client.get(
        f"{technical.BASE}/{populated['session']['id']}/sources/"
        f"{populated['upload']['source_id']}/revisions",
        headers=technical.headers,
    )
    assert history.status_code == 403


def test_platform_only_and_unauthenticated_users_get_no_access(
    client, make_user, populated
):
    assert client.get(Intake.BASE).status_code in (401, 403)
    platform = Intake(client, make_user(platform_role="platform_admin"))
    assert (
        platform.client.get(platform.BASE, headers=platform.headers).status_code == 403
    )
    assert platform.create().status_code == 403
    assert platform.get(populated["session"]["id"]).status_code == 403


def test_revoked_membership_loses_access(client, make_user, populated):
    headers = make_user(GovernanceRole.ORGANIZATION_ADMIN.value, active=False)
    assert client.get(Intake.BASE, headers=headers).status_code in (401, 403)


def test_content_reads_are_audited_without_content(api, populated, db_session):
    from rai_gateway.models.database import AdminAudit

    api.client.get(
        f"{api.BASE}/{populated['session']['id']}/sources/{populated['upload']['source_id']}"
        f"/revisions/{populated['upload']['revision_id']}/content",
        headers=api.headers,
    )
    rows = (
        db_session.query(AdminAudit)
        .filter(AdminAudit.action.like("onboarding.%"))
        .all()
    )
    actions = {r.action for r in rows}
    assert {
        "onboarding.session.create",
        "onboarding.source.add",
        "onboarding.source.content_read",
    } <= actions
    blob = json.dumps([r.target for r in rows]) + " ".join(r.reason or "" for r in rows)
    for secret in (SECRET_TITLE, SECRET_PROMPT, SECRET_FILE):
        assert secret not in blob


def test_no_response_exposes_storage_keys_or_paths(api, populated, storage_root):
    sid = populated["session"]["id"]
    responses = [
        api.get(sid),
        api.client.get(api.BASE, headers=api.headers),
        api.client.get(f"{api.BASE}/{sid}/runs", headers=api.headers),
        api.client.get(
            f"{api.BASE}/{sid}/sources/{populated['upload']['source_id']}/revisions",
            headers=api.headers,
        ),
    ]
    for response in responses:
        assert "storage_key" not in response.text
        assert str(storage_root) not in response.text
        assert "objects/" not in response.text


def test_generic_jobs_api_rejects_internal_types_and_redacts_onboarding_jobs(
    api, populated
):
    rejected = api.client.post(
        "/v1/productization/jobs",
        json={"job_type": "ONBOARDING_PREPARE_SOURCES", "payload": {"run_id": "x"}},
        headers=api.headers,
    )
    assert rejected.status_code == 422
    sneaky = api.client.post(
        "/v1/productization/jobs",
        json={
            "job_type": "REPORT",
            "idempotency_key": "onboarding:prepare:x",
            "payload": {},
        },
        headers=api.headers,
    )
    assert sneaky.status_code == 422
    allowed = api.client.post(
        "/v1/productization/jobs",
        json={"job_type": "PORTFOLIO_REPORT", "payload": {"a": 1}},
        headers=api.headers,
    )
    assert allowed.status_code == 200 and allowed.json()["payload"] == {"a": 1}
    listing = api.client.get("/v1/productization/jobs?limit=200", headers=api.headers)
    onboarding = [
        j for j in listing.json()["items"] if j["job_type"].startswith("ONBOARDING_")
    ]
    assert onboarding
    for job in onboarding:
        assert job["payload"] is None and job["result"] is None
        assert job["last_error"] is None and job["idempotency_key"] is None
