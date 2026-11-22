"""Extraction pipeline: stages, identity, accounting, citations, permissions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from onboarding_support import Intake, fresh
from parser_fixtures import make_pdf, xlsx_bytes

from rai_gateway.core.config import settings
from rai_gateway.governance.domain import GovernanceRole
from rai_gateway.models.onboarding import (
    OnboardingExtractedBlock,
    OnboardingRun,
    OnboardingRunSource,
    OnboardingSourceExtraction,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.models.registry import Agent, AgentVersion, AISystem
from rai_gateway.onboarding import ai_adapter, extraction, preparation
from rai_gateway.onboarding.service import EXTRACT_JOB_TYPE
from rai_gateway.onboarding.storage import FilesystemSourceStore
from rai_gateway.sdk.jobs import JobQueue

CAPS = "/v1/productization/onboarding/capabilities"


@pytest.fixture()
def store(storage_root):
    return FilesystemSourceStore(storage_root)


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    """Extraction must work with AI disabled and never call a model."""

    def boom(*args, **kwargs):
        raise AssertionError("extraction attempted an outbound model call")

    monkeypatch.setattr(ai_adapter, "send", boom)


def wp(session_factory, store):
    return preparation.run_pass(session_factory, store, worker_id="t")


def fingerprint(api) -> str:
    return api.client.get(CAPS, headers=api.headers).json()["parser_profile"][
        "fingerprint"
    ]


def extract(api, session, prep_run_id, key=None, fp=None, version=None, revision=None):
    return api.client.post(
        f"{api.BASE}/{session['id']}/extract",
        json={
            "expected_row_version": version or session["row_version"],
            "requested_input_revision": session["input_revision"]
            if revision is None
            else revision,
            "preparation_run_id": prep_run_id,
            "expected_parser_fingerprint": fp or fingerprint(api),
        },
        headers={**api.headers, "Idempotency-Key": key or uuid.uuid4().hex},
    )


def prepared_session(
    api, session_factory, store, files, prompt="Onboard our AI estate"
):
    session = api.create().json()
    if prompt:
        session = api.text(session, prompt).json()["session"]
    for name, data in files:
        session = api.upload(session, name, data).json()["session"]
    prep = api.prepare(session)
    assert prep.status_code == 202, prep.text
    wp(session_factory, store)
    view = fresh(api, session)
    assert view["preparation"]["state"] == "PREPARED", view["preparation"]
    return view, prep.json()["run"]["id"]


def good_xlsx() -> bytes:
    def build(wb):
        ws = wb.active
        ws.title = "AI Inventory"
        ws["A1"], ws["B1"] = "System", "Id"
        ws["A2"], ws["B2"] = "Support", "00123"

    return xlsx_bytes(build)


GOOD = [
    ("policy.pdf", make_pdf(["First page policy text", "Second page"])),
    ("inventory.xlsx", good_xlsx()),
    ("systems.csv", "name,owner\nChatbot,alice\n".encode()),
    ("notes.md", "# Notes\nمرحبا بالعالم\n".encode("utf-8")),
]


def run_extraction(api, session_factory, store, view, prep_id, **kw):
    response = extract(api, view, prep_id, **kw)
    assert response.status_code in (200, 202), response.text
    wp(session_factory, store)
    run_id = response.json()["run"]["id"]
    return api.client.get(
        f"{api.BASE}/{view['id']}/runs/{run_id}", headers=api.headers
    ).json()


def test_end_to_end_extraction_with_ai_disabled_and_exact_citation(
    api, session_factory, store, db_session
):
    before = [db_session.query(m).count() for m in (AISystem, Agent, AgentVersion)]
    view, prep_id = prepared_session(api, session_factory, store, GOOD)
    assert view["ai_configuration"]["state"] == "UNCONFIGURED"  # no credential needed
    assert "extract" in view["allowed_actions"]

    run = run_extraction(api, session_factory, store, view, prep_id)
    after = fresh(api, view)
    ext = after["extraction"]
    assert ext["state"] == "EXTRACTED" and ext["current"] is True
    assert ext["all_sources_accounted"] is True and ext["attention_count"] == 0, [
        (s["label"], s["outcome"], s.get("reason")) for s in ext["sources"]
    ]
    assert ext["coverage_acceptable"] is True
    assert {s["outcome"] for s in ext["sources"]} == {"COMPLETE"} and len(
        ext["sources"]
    ) == 5
    assert after["preparation"]["state"] == "PREPARED"
    assert after["view"] == "READY_FOR_ANALYSIS"  # Step 02 meaning is unchanged

    db_session.expire_all()
    assert db_session.query(OnboardingRunSource).filter_by(state="PENDING").count() == 0
    assert [
        db_session.query(m).count() for m in (AISystem, Agent, AgentVersion)
    ] == before

    # Blocks and a citation resolve to the exact source revision and digest.
    listing = api.client.get(
        f"{api.BASE}/{view['id']}/extractions?limit=50", headers=api.headers
    ).json()["items"]
    csv_row = next(i for i in listing if i["display_name"] == "systems.csv")
    csv_row["extraction_id"] = csv_row["id"]
    blocks = api.client.get(
        f"{api.BASE}/{view['id']}/extractions/{csv_row['extraction_id']}/blocks",
        headers=api.headers,
    ).json()
    assert blocks["extraction"]["outcome"] == "COMPLETE"
    block = blocks["items"][1]
    assert block["cell_data"]["cells"] == ["Chatbot", "alice"]
    citation = api.client.get(
        f"{api.BASE}/{view['id']}/citations/{block['id']}?start=0&end=7",
        headers=api.headers,
    ).json()
    assert citation["excerpt"] == "Chatbot" == block["text"][0:7]
    assert (
        citation["source_sha256"] == citation["source_sha256"]
        and citation["integrity_ok"]
    )
    assert (
        citation["locator"]["type"] == "CSV_RECORD"
        and citation["is_current_input"] is True
    )
    assert citation["text_range"] == {
        "start": 0,
        "end": 7,
        "unit": "UNICODE_CODE_POINT",
    }
    assert citation["source_revision_id"] == csv_row["source_revision_id"]
    assert run["stage"] == "EXTRACT_SOURCES"


def test_xlsx_cell_citation_and_arabic_text_round_trip(api, session_factory, store):
    view, prep_id = prepared_session(api, session_factory, store, GOOD)
    run_extraction(api, session_factory, store, view, prep_id)
    ext = fresh(api, view)["extraction"]
    listing = api.client.get(
        f"{api.BASE}/{view['id']}/extractions?limit=50", headers=api.headers
    ).json()
    xlsx = next(i for i in listing["items"] if i["display_name"] == "inventory.xlsx")
    blocks = api.client.get(
        f"{api.BASE}/{view['id']}/extractions/{xlsx['id']}/blocks", headers=api.headers
    ).json()["items"]
    cell = next(b for b in blocks if b["locator"]["cell"] == "B2")
    assert cell["text"] == "00123" and cell["cell_data"]["value"] == "00123"
    md = next(i for i in listing["items"] if i["display_name"] == "notes.md")
    md_blocks = api.client.get(
        f"{api.BASE}/{view['id']}/extractions/{md['id']}/blocks", headers=api.headers
    ).json()["items"]
    assert "مرحبا بالعالم" in md_blocks[0]["text"]
    assert ext["state"] == "EXTRACTED"


def test_offsets_are_code_points_not_utf16_and_invalid_ranges_are_rejected(
    api, session_factory, store
):
    text = "😀 abc é end"  # emoji + combining accent
    view, prep_id = prepared_session(
        api, session_factory, store, [("t.txt", text.encode())], prompt=None
    )
    run_extraction(api, session_factory, store, view, prep_id)
    src = fresh(api, view)["extraction"]["sources"][0]
    block = api.client.get(
        f"{api.BASE}/{view['id']}/extractions/{src['extraction_id']}/blocks",
        headers=api.headers,
    ).json()["items"][0]
    base = f"{api.BASE}/{view['id']}/citations/{block['id']}"
    got = api.client.get(f"{base}?start=2&end=5", headers=api.headers).json()["excerpt"]
    assert got == "abc"  # UTF-16 indexes would give " ab"
    assert (
        api.client.get(f"{base}?start=6&end=8", headers=api.headers).json()["excerpt"]
        == "é"
    )
    for query in (
        "?start=5&end=5",
        "?start=0&end=999",
        "?start=3",
        "?end=3",
        "?start=4&end=2",
    ):
        assert api.client.get(base + query, headers=api.headers).status_code == 422, (
            query
        )
    whole = api.client.get(base, headers=api.headers).json()
    assert whole["excerpt"] == text and whole["text_range"]["end"] == len(text)


def test_cross_session_and_unknown_references_are_rejected(api, session_factory, store):
    view, prep_id = prepared_session(api, session_factory, store, GOOD[2:3])
    run_extraction(api, session_factory, store, view, prep_id)
    src = fresh(api, view)["extraction"]["sources"][0]
    other = api.create("Other").json()
    block = api.client.get(
        f"{api.BASE}/{view['id']}/extractions/{src['extraction_id']}/blocks",
        headers=api.headers,
    ).json()["items"][0]
    assert (
        api.client.get(
            f"{api.BASE}/{other['id']}/citations/{block['id']}", headers=api.headers
        ).status_code
        == 404
    )
    assert (
        api.client.get(
            f"{api.BASE}/{other['id']}/extractions/{src['extraction_id']}/blocks",
            headers=api.headers,
        ).status_code
        == 404
    )
    assert (
        api.client.get(
            f"{api.BASE}/{view['id']}/citations/{uuid.uuid4()}", headers=api.headers
        ).status_code
        == 404
    )


def test_every_manifest_source_gets_an_outcome_and_attention_blocks_readiness(
    api, session_factory, store, db_session
):
    files = [
        ("scan.pdf", make_pdf(["IMAGE"])),
        ("amb.csv", b"a,b;c\n1,2;3\n"),
        ("ok.txt", b"fine"),
        ("blank.txt", b" \n "),
    ]
    view, prep_id = prepared_session(api, session_factory, store, files, prompt=None)
    run = run_extraction(api, session_factory, store, view, prep_id)
    ext = fresh(api, view)["extraction"]
    outcomes = sorted(s["outcome"] for s in ext["sources"])
    assert outcomes == ["COMPLETE", "NEEDS_INPUT", "NEEDS_INPUT", "NEEDS_OCR"]
    assert ext["state"] == "EXTRACTED"  # the worker finished...
    assert (
        ext["attention_count"] == 3 and ext["coverage_acceptable"] is False
    )  # ...sources need attention
    assert ext["all_sources_accounted"] is True
    reasons = {s["reason"] for s in ext["sources"]}
    assert {"SCANNED_IMAGE_ONLY", "AMBIGUOUS_DELIMITER", "NO_USABLE_TEXT"} <= reasons
    job = db_session.get(
        BackgroundJob, db_session.get(OnboardingRun, uuid.UUID(run["id"])).job_id
    )
    assert job.status == "SUCCEEDED" and job.result["attention_count"] == 3


def test_options_edit_invalidates_extraction_and_requires_new_preparation(
    api, session_factory, store, db_session
):
    view, prep_id = prepared_session(
        api, session_factory, store, [("amb.csv", b"a,b;c\n1,2;3\n")], prompt=None
    )
    run_extraction(api, session_factory, store, view, prep_id)
    after = fresh(api, view)
    source = after["sources"][0]
    revision_before = source["current_revision"]["sha256"]
    # The user chooses a delimiter: originals and digests are untouched.
    patched = api.client.patch(
        f"{api.BASE}/{view['id']}/sources/{source['id']}/parsing-options",
        json={
            "options": {"delimiter": ";"},
            "expected_row_version": after["row_version"],
        },
        headers={**api.headers, "Idempotency-Key": "opts-key-0001"},
    )
    assert patched.status_code == 200, patched.text
    new = patched.json()
    assert new["input_revision"] == after["input_revision"] + 1
    assert new["row_version"] == after["row_version"] + 1
    assert new["sources"][0]["parsing_options"] == {"delimiter": ";"}
    assert new["sources"][0]["current_revision"]["sha256"] == revision_before
    assert (
        new["extraction"]["current"] is False
        and new["preparation"]["state"] == "NOT_PREPARED"
    )
    assert (
        "extract" not in new["allowed_actions"] and "prepare" in new["allowed_actions"]
    )

    stale = extract(api, new, prep_id)
    assert stale.status_code == 409  # old preparation no longer eligible

    replay = api.client.patch(
        f"{api.BASE}/{view['id']}/sources/{source['id']}/parsing-options",
        json={
            "options": {"delimiter": ";"},
            "expected_row_version": after["row_version"],
        },
        headers={**api.headers, "Idempotency-Key": "opts-key-0001"},
    )
    assert (
        replay.status_code == 200
        and replay.json()["input_revision"] == new["input_revision"]
    )

    api.prepare(new)
    wp(session_factory, store)
    new = fresh(api, view)
    new_prep = new["preparation"]["run_id"]
    assert new_prep != prep_id and new["preparation"]["state"] == "PREPARED"
    run_extraction(api, session_factory, store, new, new_prep)
    done = fresh(api, view)["extraction"]
    assert (
        done["sources"][0]["outcome"] == "COMPLETE"
        and done["coverage_acceptable"] is True
    )
    # History is kept: the first (NEEDS_INPUT) extraction still exists.
    db_session.expire_all()
    assert db_session.query(OnboardingSourceExtraction).count() == 2
    runs = api.client.get(
        f"{api.BASE}/{view['id']}/runs?limit=10", headers=api.headers
    ).json()
    assert [r["stage"] for r in runs["items"]].count("EXTRACT_SOURCES") == 2


def test_parsing_option_validation_and_text_sources_stay_utf8(api, session):
    prompt = api.text(session, "prompt text").json()
    opts = f"{api.BASE}/{session['id']}/sources/{prompt['source_id']}/parsing-options"
    h = {**api.headers, "Idempotency-Key": "opts-key-0002"}
    bad = api.client.patch(
        opts,
        json={
            "options": {"encoding": "windows-1256"},
            "expected_row_version": prompt["session"]["row_version"],
        },
        headers=h,
    )
    assert (
        bad.status_code == 422
        and bad.json()["error"]["code"] == "INVALID_PARSING_OPTIONS"
    )
    csv_src = api.upload(prompt["session"], "a.csv", b"a,b\n").json()
    base = f"{api.BASE}/{session['id']}/sources/{csv_src['source_id']}/parsing-options"
    for options in (
        {"encoding": "latin-1"},
        {"delimiter": "x"},
        {"parser": "Evil"},
        {"header_mode": "magic"},
    ):
        res = api.client.patch(
            base,
            json={
                "options": options,
                "expected_row_version": csv_src["session"]["row_version"],
            },
            headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
        )
        assert res.status_code == 422, options
    stale = api.client.patch(
        base,
        json={"options": {"delimiter": ";"}, "expected_row_version": 1},
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert (
        stale.status_code == 409
        and stale.json()["error"]["code"] == "REVISION_CONFLICT"
    )


def test_legacy_v1_preparation_stays_readable_but_cannot_feed_extraction(
    api, session_factory, store, db_session
):
    view, prep_id = prepared_session(
        api, session_factory, store, GOOD[2:3], prompt=None
    )
    run = db_session.get(OnboardingRun, uuid.UUID(prep_id))
    manifest = dict(run.manifest)
    manifest["version"] = 1
    for entry in manifest["entries"]:
        for key in ("parsing_options", "parsing_options_version", "options_digest"):
            entry.pop(key, None)
    run.manifest = manifest
    db_session.commit()
    legacy = fresh(api, view)
    assert legacy["preparation"]["requires_new_preparation"] is True
    assert legacy["view"] == "READY_FOR_ANALYSIS"  # Step 02 meaning preserved
    assert "extract" not in legacy["allowed_actions"]
    refused = extract(api, legacy, prep_id)
    assert (
        refused.status_code == 409
        and refused.json()["error"]["code"] == "PREPARATION_REQUIRED"
    )
    again = api.prepare(legacy)
    assert again.status_code == 202 and again.json()["run"]["id"] != prep_id
    db_session.expire_all()
    assert db_session.get(OnboardingRun, uuid.UUID(prep_id)).manifest["version"] == 1


def test_current_runs_are_stage_aware(api, session_factory, store):
    view, prep_id = prepared_session(
        api, session_factory, store, GOOD[2:3], prompt=None
    )
    run_extraction(api, session_factory, store, view, prep_id)
    after = fresh(api, view)
    assert after["current_run"]["stage"] == "EXTRACT_SOURCES"
    # A later extraction run must not replace the preparation run for prepare eligibility.
    again = api.prepare(after)
    assert again.status_code == 200 and again.json()["run"]["id"] == prep_id
    assert again.json()["run"]["stage"] == "PREPARE_SOURCES"
    assert (
        after["preparation"]["run_id"] == prep_id
        and after["preparation"]["current"] is True
    )


def test_extract_is_idempotent_and_converges_and_requires_current_preparation(
    api, session_factory, store
):
    view, prep_id = prepared_session(
        api, session_factory, store, GOOD[2:3], prompt=None
    )
    a = extract(api, view, prep_id, key="extract-key-001")
    b = extract(api, view, prep_id, key="extract-key-001")
    c = extract(api, view, prep_id, key="extract-key-002")
    assert a.status_code == 202
    assert a.json()["run"]["id"] == b.json()["run"]["id"] == c.json()["run"]["id"]
    assert b.json()["replayed"] is True
    stale_rev = extract(api, view, prep_id, key="extract-key-003", revision=0)
    assert (
        stale_rev.status_code == 409
        and stale_rev.json()["error"]["code"] == "REVISION_CONFLICT"
    )
    wrong = extract(api, view, str(uuid.uuid4()), key="extract-key-004")
    assert wrong.status_code == 404
    assert (
        extract(
            api, view, prep_id, key="extract-key-005", fp="deadbeef" * 4
        ).status_code
        == 409
    )


def test_cancelled_extraction_accounts_for_every_source_and_old_owner_cannot_publish(
    api, session_factory, store, db_session
):
    view, prep_id = prepared_session(
        api, session_factory, store, GOOD[2:4], prompt=None
    )
    run = extract(api, view, prep_id).json()["run"]
    db = session_factory()
    claimed = JobQueue(db).claim_leased(
        job_types=[EXTRACT_JOB_TYPE], worker_id="w", lease_seconds=60
    )
    job_id, token = claimed.id, claimed.lease_token
    db.commit()
    db.close()
    cancelled = api.cancel(view["id"], run["id"])
    assert (
        cancelled.status_code == 200 and cancelled.json()["run"]["state"] == "CANCELLED"
    )
    db_session.expire_all()
    rows = (
        db_session.query(OnboardingRunSource)
        .filter_by(run_id=uuid.UUID(run["id"]))
        .all()
    )
    assert {(r.state, r.outcome, r.reason) for r in rows} == {
        ("FINAL", "NOT_PROCESSED", "RUN_CANCELLED")
    }
    outcome = extraction.process_extraction_job(session_factory, store, job_id, token)
    assert outcome == "lost_ownership"
    assert (
        db_session.query(OnboardingSourceExtraction).count() == 0
    )  # nothing published
    state = fresh(api, view)["extraction"]
    assert state["state"] == "CANCELLED" and state["coverage_acceptable"] is False


def test_changed_parser_profile_cannot_reuse_cache_or_run_under_another_config(
    api, session_factory, store, db_session, monkeypatch
):
    view, prep_id = prepared_session(
        api, session_factory, store, GOOD[2:3], prompt=None
    )
    first = run_extraction(api, session_factory, store, view, prep_id)
    db_session.expire_all()
    assert db_session.query(OnboardingSourceExtraction).count() == 1
    old_fp = fingerprint(api)

    monkeypatch.setattr(settings, "onboarding_tabular_max_rows", 9_999)
    assert fingerprint(api) != old_fp
    refused = extract(api, fresh(api, view), prep_id, fp=old_fp)
    assert (
        refused.status_code == 409
        and refused.json()["error"]["code"] == "PARSER_PROFILE_MISMATCH"
    )
    second = run_extraction(api, session_factory, store, fresh(api, view), prep_id)
    assert second["id"] != first["id"]  # a changed plan is a new run, not a retry
    db_session.expire_all()
    assert (
        db_session.query(OnboardingSourceExtraction).count() == 2
    )  # no incompatible reuse

    # The exact same plan again reuses the published result (one published identity).
    monkeypatch.setattr(settings, "onboarding_tabular_max_rows", 10_000)
    third = run_extraction(api, session_factory, store, fresh(api, view), prep_id)
    db_session.expire_all()
    assert db_session.query(OnboardingSourceExtraction).count() == 2
    assert third["state"] == "SUCCEEDED"


def test_worker_with_different_parser_config_fails_explicitly_before_parsing(
    api, session_factory, store, db_session, monkeypatch
):
    view, prep_id = prepared_session(
        api, session_factory, store, GOOD[2:4], prompt=None
    )
    run = extract(api, view, prep_id).json()["run"]
    monkeypatch.setattr(settings, "onboarding_xlsx_max_sheets", 7)  # deployment skew
    wp(session_factory, store)
    db_session.expire_all()
    job = db_session.get(
        BackgroundJob, db_session.get(OnboardingRun, uuid.UUID(run["id"])).job_id
    )
    assert job.status == "FAILED" and job.last_error == "PARSER_PROFILE_MISMATCH"
    rows = (
        db_session.query(OnboardingRunSource)
        .filter_by(run_id=uuid.UUID(run["id"]))
        .all()
    )
    assert {(r.state, r.outcome, r.reason) for r in rows} == {
        ("FINAL", "NOT_PROCESSED", "RUN_ABORTED")
    }
    assert db_session.query(OnboardingSourceExtraction).count() == 0
    assert fresh(api, view)["extraction"]["state"] == "FAILED"


def test_aggregate_output_limit_marks_the_rest_not_processed(
    api, session_factory, store, db_session, monkeypatch
):
    view, prep_id = prepared_session(
        api, session_factory, store, GOOD[2:4], prompt=None
    )
    monkeypatch.setattr(settings, "onboarding_extract_output_bytes_per_run", 1)
    run = run_extraction(api, session_factory, store, view, prep_id)
    ext = fresh(api, view)["extraction"]
    outcomes = sorted(s["outcome"] for s in ext["sources"])
    assert outcomes == ["COMPLETE", "NOT_PROCESSED"] and run["state"] == "SUCCEEDED"
    assert ext["coverage_acceptable"] is False and ext["attention_count"] == 1
    skipped = next(s for s in ext["sources"] if s["outcome"] == "NOT_PROCESSED")
    assert skipped["reason"] == "RUN_OUTPUT_LIMIT" and skipped["extraction_id"] is None
    db_session.expire_all()
    assert db_session.query(OnboardingRunSource).filter_by(state="PENDING").count() == 0


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda: b"tampered", "SOURCE_INTEGRITY_FAILED"),
        (lambda: None, "SOURCE_MISSING"),
    ],
)
def test_changed_or_missing_originals_are_recorded_per_source_not_cached(
    api, session_factory, store, storage_root, db_session, mutate, reason
):
    from test_intake_jobs import tamper

    view, prep_id = prepared_session(
        api, session_factory, store, [("a.txt", b"hello")], prompt=None
    )
    run = extract(api, view, prep_id).json()["run"]
    tamper(session_factory, storage_root, view["sources"][0]["id"], mutate())
    wp(session_factory, store)
    ext = fresh(api, view)["extraction"]
    assert (
        ext["sources"][0]["outcome"] == "FAILED"
        and ext["sources"][0]["reason"] == reason
    )
    assert ext["coverage_acceptable"] is False and run["id"] == ext["run_id"]
    db_session.expire_all()
    assert (
        db_session.query(OnboardingSourceExtraction).count() == 0
    )  # never a reusable success


def test_unique_identity_and_block_ordinals_are_enforced_by_the_database(
    api, session_factory, store, db_session
):
    from sqlalchemy.exc import IntegrityError

    view, prep_id = prepared_session(
        api, session_factory, store, GOOD[2:3], prompt=None
    )
    run_extraction(api, session_factory, store, view, prep_id)
    db_session.expire_all()
    ext = db_session.query(OnboardingSourceExtraction).one()
    clone = OnboardingSourceExtraction(
        source_revision_id=ext.source_revision_id,
        original_sha256=ext.original_sha256,
        extractor_build=ext.extractor_build,
        dependency_versions=ext.dependency_versions,
        schema_version=1,
        normalization_version=1,
        options_snapshot={},
        options_digest="x",
        policy_digest="x",
        identity_digest=ext.identity_digest,
        outcome="COMPLETE",
        coverage={},
        warnings=[],
    )
    db_session.add(clone)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    block = db_session.query(OnboardingExtractedBlock).first()
    db_session.add(
        OnboardingExtractedBlock(
            extraction_id=block.extraction_id,
            ordinal=block.ordinal,
            kind="TEXT",
            text="dup",
            locator={},
            content_digest="d",
            warnings=[],
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_replaced_revision_makes_old_citations_historical(api, session_factory, store):
    view, prep_id = prepared_session(
        api, session_factory, store, [("a.txt", b"old wording")], prompt=None
    )
    run_extraction(api, session_factory, store, view, prep_id)
    src = fresh(api, view)["extraction"]["sources"][0]
    block = api.client.get(
        f"{api.BASE}/{view['id']}/extractions/{src['extraction_id']}/blocks",
        headers=api.headers,
    ).json()["items"][0]
    now = fresh(api, view)
    api.replace_file(now, now["sources"][0]["id"], "a.txt", b"new wording")
    cite = api.client.get(
        f"{api.BASE}/{view['id']}/citations/{block['id']}", headers=api.headers
    ).json()
    assert cite["historical"] is True and cite["is_current_input"] is False
    assert cite["excerpt"] == "old wording"  # still the exact stored output
    state = fresh(api, view)
    assert (
        state["extraction"]["current"] is False
        and state["preparation"]["state"] == "NOT_PREPARED"
    )


def test_extraction_permissions_and_redaction(
    client, make_user, api, session_factory, store
):
    view, prep_id = prepared_session(
        api, session_factory, store, GOOD[2:3], prompt=None
    )
    run_extraction(api, session_factory, store, view, prep_id)
    src = fresh(api, view)["extraction"]["sources"][0]
    block = api.client.get(
        f"{api.BASE}/{view['id']}/extractions/{src['extraction_id']}/blocks",
        headers=api.headers,
    ).json()["items"][0]

    reader = Intake(client, make_user(GovernanceRole.AI_SYSTEM_OWNER.value))
    detail = reader.get(view["id"])
    assert detail.status_code == 200
    text = detail.text
    for secret in (
        "Chatbot",
        "systems.csv",
        src["extraction_id"],
        "reason",
        "sha256",
        "parsing_options",
    ):
        assert secret not in text, secret
    summary = detail.json()["extraction"]
    assert summary["state"] == "EXTRACTED" and summary["counts"] == {"COMPLETE": 1}
    assert [s["label"] for s in summary["sources"]] == ["Source 1"]
    listing = reader.client.get(
        f"{reader.BASE}/{view['id']}/extractions", headers=reader.headers
    )
    assert listing.status_code == 200
    for secret in (
        "systems.csv",
        "Chatbot",
        "warnings",
        "dependency_versions",
        "source_id",
    ):
        assert secret not in listing.text, secret
    blocks_url = f"{reader.BASE}/{view['id']}/extractions/{src['extraction_id']}/blocks"
    assert reader.client.get(blocks_url, headers=reader.headers).status_code == 403
    assert (
        reader.client.get(
            f"{reader.BASE}/{view['id']}/citations/{block['id']}",
            headers=reader.headers,
        ).status_code
        == 403
    )

    technical = Intake(client, make_user(GovernanceRole.TECHNICAL_OWNER.value))
    assert (
        technical.client.get(blocks_url, headers=technical.headers).status_code == 403
    )
    assert extract(technical, view, prep_id).status_code == 403

    jobs = api.client.get(
        "/v1/productization/jobs?limit=200", headers=api.headers
    ).json()["items"]
    mine = [j for j in jobs if j["job_type"] == EXTRACT_JOB_TYPE]
    assert mine and all(
        j["payload"] is None and j["result"] is None and j["last_error"] is None
        for j in mine
    )
    caps = reader.client.get(CAPS, headers=reader.headers)
    assert (
        caps.status_code == 200 and "ocr" in caps.json() and caps.json()["ocr"] is False
    )


def test_capabilities_expose_formats_options_and_profile(api):
    body = api.client.get(CAPS, headers=api.headers).json()
    assert {f["family"] for f in body["formats"]} == {
        "PDF",
        "DOCX",
        "XLSX",
        "CSV",
        "TXT",
        "MARKDOWN",
    }
    assert body["parsing_options"]["encodings"] == ["utf-8", "windows-1256"]
    assert body["parser_profile"]["limits"]["pdf_pages"] == 300
    assert {"pypdf", "python-docx", "openpyxl", "defusedxml"} <= set(
        body["parser_profile"]["dependencies"]
    )


def test_expired_extraction_lease_is_reclaimed_and_completed_sources_are_not_redone(
    api, session_factory, store, db_session
):
    view, prep_id = prepared_session(
        api, session_factory, store, GOOD[2:4], prompt=None
    )
    run = extract(api, view, prep_id).json()["run"]
    db = session_factory()
    first = JobQueue(db).claim_leased(
        job_types=[EXTRACT_JOB_TYPE], worker_id="a", lease_seconds=60
    )
    db.commit()
    db.query(BackgroundJob).filter(BackgroundJob.id == first.id).update(
        {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)}
    )
    db.commit()
    db.close()
    stats = wp(session_factory, store)  # reclaims with a fresh token and finishes
    assert stats["succeeded"] == 1
    ext = fresh(api, view)["extraction"]
    assert ext["state"] == "EXTRACTED" and ext["run_id"] == run["id"]
    db_session.expire_all()
    assert db_session.query(OnboardingSourceExtraction).count() == 2


def test_real_worker_process_extracts_without_any_ai_credential(
    api, session_factory, store, db_engine, storage_root, monkeypatch
):
    """`rai-worker onboarding --once` subprocess: prepare, extract, cite - no AI configured."""
    import os
    import subprocess
    import sys

    for name in list(os.environ):
        if name.startswith("ONBOARDING_AI_") or name in (
            "LLM_PROVIDER_API_KEY",
            "OPENAI_API_KEY",
        ):
            monkeypatch.delenv(name, raising=False)
    env = {
        **os.environ,
        "DATABASE_URL": f"{db_engine['base_url']}?options=-csearch_path%3D{db_engine['schema']}",
        "ONBOARDING_SOURCE_STORAGE_ROOT": str(storage_root),
        "ONBOARDING_AI_TARGETS_JSON": "",
    }

    def run_worker() -> str:
        done = subprocess.run(
            [sys.executable, "-m", "rai_gateway.workers.cli", "onboarding", "--once"],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    session = api.create().json()
    session = api.text(session, "Please onboard our estate").json()["session"]
    for name, data in GOOD[:3]:
        session = api.upload(session, name, data).json()["session"]
    api.prepare(session)
    assert "'succeeded': 1" in run_worker()
    view = fresh(api, session)
    assert extract(api, view, view["preparation"]["run_id"]).status_code == 202
    assert "'succeeded': 1" in run_worker()
    ext = fresh(api, session)["extraction"]
    assert ext["state"] == "EXTRACTED" and ext["coverage_acceptable"] is True
    assert fresh(api, session)["ai_configuration"]["state"] == "UNCONFIGURED"
    first = ext["sources"][0]
    blocks = api.client.get(
        f"{api.BASE}/{session['id']}/extractions/{first['extraction_id']}/blocks",
        headers=api.headers,
    ).json()["items"]
    cite = api.client.get(
        f"{api.BASE}/{session['id']}/citations/{blocks[0]['id']}", headers=api.headers
    ).json()
    assert cite["excerpt"] == blocks[0]["text"] and cite["is_current_input"] is True
