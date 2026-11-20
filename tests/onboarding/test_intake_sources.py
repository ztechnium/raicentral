"""Sessions, original sources, revisions, idempotency and limits."""

from __future__ import annotations

import hashlib
import os

import pytest

from conftest import CSV, DOCX, MD, PDF, XLSX, fresh  # noqa: F401
from rai_gateway.core.config import settings
from rai_gateway.models.onboarding import (
    OnboardingSource,
    OnboardingSourceRevision,
)


def _content(api, session_id, source_id, revision_id):
    return api.client.get(
        f"{api.BASE}/{session_id}/sources/{source_id}/revisions/{revision_id}/content",
        headers=api.headers,
    )


def test_session_create_is_idempotent_and_conflicts_on_changed_request(api):
    first = api.create("Alpha", key="create-key-0001")
    again = api.create("Alpha", key="create-key-0001")
    other = api.create("Beta", key="create-key-0001")
    assert first.status_code == 201 and again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert first.json()["row_version"] == 1 and first.json()["input_revision"] == 0
    assert first.json()["view"] == "DRAFT"


def test_opening_the_list_creates_nothing(api):
    before = api.client.get(api.BASE, headers=api.headers).json()["total"]
    api.client.get(api.BASE, headers=api.headers)
    assert api.client.get(api.BASE, headers=api.headers).json()["total"] == before


@pytest.mark.parametrize(
    "name,data",
    [
        ("policy.pdf", PDF),
        ("policy.docx", DOCX),
        ("inventory.xlsx", XLSX),
        ("inventory.csv", CSV),
        ("notes.txt", b"plain notes\n"),
        ("readme.md", MD),
    ],
)
def test_supported_originals_round_trip_exactly(api, session, name, data):
    response = api.upload(session, name, data)
    assert response.status_code == 201, response.text
    body = response.json()
    source = body["session"]["sources"][0]
    revision = source["current_revision"]
    assert revision["byte_length"] == len(data)
    assert revision["sha256"] == hashlib.sha256(data).hexdigest()
    assert revision["display_name"] == name
    download = _content(api, session["id"], body["source_id"], body["revision_id"])
    assert download.status_code == 200
    assert download.content == data
    assert download.headers["x-content-type-options"] == "nosniff"
    assert download.headers["content-disposition"].startswith("attachment")
    assert body["session"]["input_revision"] == 1
    assert body["session"]["row_version"] == 2


@pytest.mark.parametrize(
    "name,data,ctype",
    [
        ("tool.exe", b"MZ\x90\x00", "application/octet-stream"),
        ("legacy.doc", b"\xd0\xcf\x11\xe0", "application/msword"),
        ("legacy.xls", b"\xd0\xcf\x11\xe0", "application/vnd.ms-excel"),
        ("bundle.zip", DOCX, "application/zip"),
        ("scan.png", b"\x89PNG\r\n", "image/png"),
        ("fake.pdf", b"this is not a pdf", "application/pdf"),
        ("fake.docx", b"PK\x03\x04junk", "application/octet-stream"),
        ("fake.xlsx", DOCX, "application/octet-stream"),
        ("binary.txt", b"abc\x00def", "text/plain"),
        ("mismatch.pdf", PDF, "image/png"),
        ("empty.txt", b"", "text/plain"),
    ],
)
def test_unsupported_or_mislabelled_files_are_rejected_without_a_source(
    api, session, db_session, name, data, ctype
):
    response = api.upload(session, name, data, ctype)
    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA"
    assert fresh(api, session)["total_source_count"] == 0
    assert db_session.query(OnboardingSource).count() == 0


def test_oversized_file_is_rejected_and_leaves_nothing(
    api, session, db_session, monkeypatch, storage_root
):
    monkeypatch.setattr(settings, "onboarding_max_file_bytes", 1024)
    response = api.upload(session, "big.txt", b"a" * 2048)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "SOURCE_TOO_LARGE"
    assert db_session.query(OnboardingSourceRevision).count() == 0
    staging = storage_root / "staging"
    assert not list(staging.glob("*")) if staging.exists() else True


def test_chunked_or_lying_content_length_cannot_bypass_the_body_limit(
    api, session, monkeypatch
):
    monkeypatch.setattr(settings, "onboarding_max_file_bytes", 1024)
    monkeypatch.setattr(settings, "onboarding_multipart_overhead_bytes", 1024)

    def body():  # no Content-Length: transferred chunked
        yield b'--b\r\nContent-Disposition: form-data; name="expected_row_version"\r\n\r\n2\r\n'
        yield (
            b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n"
        )
        for _ in range(64):
            yield b"x" * 1024
        yield b"\r\n--b--\r\n"

    response = api.client.post(
        f"{api.BASE}/{session['id']}/sources/files",
        content=body(),
        headers={
            **api.headers,
            "Idempotency-Key": "chunked-key-1",
            "Content-Type": "multipart/form-data; boundary=b",
        },
    )
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "SOURCE_TOO_LARGE"
    assert fresh(api, session)["total_source_count"] == 0

    declared = api.client.post(
        f"{api.BASE}/{session['id']}/sources/files",
        content=b"x" * 10,
        headers={
            **api.headers,
            "Idempotency-Key": "lying-key-001",
            "Content-Type": "multipart/form-data; boundary=b",
            "Content-Length": "999999999",
        },
    )
    assert declared.status_code in (400, 413)


def test_extra_multipart_file_parts_are_rejected(api, session):
    response = api.client.post(
        f"{api.BASE}/{session['id']}/sources/files",
        files=[
            ("file", ("a.txt", b"one", "text/plain")),
            ("file", ("b.txt", b"two", "text/plain")),
        ],
        data={"expected_row_version": "1"},
        headers={**api.headers, "Idempotency-Key": "extra-parts-1"},
    )
    assert response.status_code == 400
    assert fresh(api, session)["total_source_count"] == 0


def test_text_is_stored_exactly_without_trimming(api, session):
    prompt = "  Line one\n\n  Line two ü \n"
    response = api.text(session, prompt)
    assert response.status_code == 201, response.text
    body = response.json()
    stored = _content(api, session["id"], body["source_id"], body["revision_id"])
    assert stored.content == prompt.encode("utf-8")
    assert stored.headers["content-type"].startswith("text/plain")


def test_empty_text_is_rejected_and_second_prompt_conflicts(api, session):
    assert api.text(session, "   \n ").status_code == 422
    first = api.text(session, "Our first prompt")
    assert first.status_code == 201
    second = api.text(first.json()["session"], "Another prompt")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "PROMPT_EXISTS"
    supporting = api.text(first.json()["session"], "Notes", role="SUPPORTING_MATERIAL")
    assert supporting.status_code == 201


def test_text_over_the_character_limit_is_rejected(api, session, monkeypatch):
    monkeypatch.setattr(settings, "onboarding_max_text_chars", 10)
    assert api.text(session, "x" * 11).status_code == 413


def test_replacement_creates_a_new_revision_and_preserves_the_old_original(
    api, session
):
    first = api.upload(session, "policy.txt", b"version one").json()
    second = api.replace_file(
        first["session"], first["source_id"], "policy.txt", b"version two"
    )
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["source_id"] == first["source_id"]
    assert body["session"]["input_revision"] == 2
    assert body["session"]["sources"][0]["current_revision"]["revision_number"] == 2
    history = api.client.get(
        f"{api.BASE}/{session['id']}/sources/{first['source_id']}/revisions",
        headers=api.headers,
    ).json()["items"]
    assert [h["revision_number"] for h in history] == [2, 1]
    assert [h["current"] for h in history] == [True, False]
    old = _content(api, session["id"], first["source_id"], first["revision_id"])
    assert old.content == b"version one"


def test_text_revision_cannot_replace_a_file_source(api, session):
    uploaded = api.upload(session, "a.txt", b"hello").json()
    response = api.revise_text(uploaded["session"], uploaded["source_id"], "text")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "KIND_MISMATCH"


def test_stale_expected_row_version_cannot_overwrite_but_identical_retry_succeeds(
    api, session
):
    first = api.upload(session, "a.txt", b"first", key="upload-key-aaa1")
    assert first.status_code == 201
    stale = api.upload(
        session, "b.txt", b"second", key="upload-key-bbb2"
    )  # old version
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
    assert fresh(api, session)["total_source_count"] == 1
    replay = api.upload(
        session, "a.txt", b"first", key="upload-key-aaa1"
    )  # stale version, same key
    assert replay.status_code == 201
    assert replay.json()["revision_id"] == first.json()["revision_id"]
    assert replay.json()["replayed"] is True
    assert fresh(api, session)["total_source_count"] == 1


def test_same_key_with_different_bytes_is_an_idempotency_conflict(api, session):
    assert (
        api.upload(session, "a.txt", b"first", key="upload-key-ccc3").status_code == 201
    )
    conflict = api.upload(session, "a.txt", b"other", key="upload-key-ccc3")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_missing_idempotency_key_and_version_are_rejected(api, session):
    no_key = api.client.post(
        f"{api.BASE}/{session['id']}/sources/files",
        files={"file": ("a.txt", b"x", "text/plain")},
        data={"expected_row_version": "1"},
        headers=api.headers,
    )
    assert no_key.status_code == 422
    no_version = api.client.post(
        f"{api.BASE}/{session['id']}/sources/files",
        files={"file": ("a.txt", b"x", "text/plain")},
        headers={**api.headers, "Idempotency-Key": "no-version-key1"},
    )
    assert no_version.status_code == 422
    no_json_version = api.client.post(
        f"{api.BASE}/{session['id']}/sources/text",
        json={"role": "USER_PROMPT", "text": "hi"},
        headers={**api.headers, "Idempotency-Key": "no-version-key2"},
    )
    assert no_json_version.status_code == 422


def test_source_count_and_retained_byte_quotas(api, session, monkeypatch):
    monkeypatch.setattr(settings, "onboarding_max_sources_per_session", 2)
    one = api.upload(session, "a.txt", b"a").json()
    two = api.upload(one["session"], "b.txt", b"b").json()
    third = api.upload(two["session"], "c.txt", b"c")
    assert third.status_code == 409
    assert third.json()["error"]["code"] == "SESSION_LIMIT_EXCEEDED"

    monkeypatch.setattr(settings, "onboarding_max_sources_per_session", 20)
    monkeypatch.setattr(settings, "onboarding_max_session_bytes", 4)
    over = api.replace_file(two["session"], one["source_id"], "a.txt", b"12345")
    assert over.status_code == 409  # retained bytes include older revisions


def test_exclusion_bumps_input_revision_and_keeps_history(api, session):
    uploaded = api.upload(session, "a.txt", b"keep me").json()
    excluded = api.include(uploaded["session"], uploaded["source_id"], False)
    assert excluded.status_code == 200
    view = excluded.json()
    assert view["input_revision"] == 2 and view["source_count"] == 0
    assert view["total_source_count"] == 1
    again = api.include(view, uploaded["source_id"], True)
    assert again.json()["source_count"] == 1
    old = _content(api, session["id"], uploaded["source_id"], uploaded["revision_id"])
    assert old.content == b"keep me"


def test_title_edit_and_archive_do_not_change_input_revision(api, session):
    renamed = api.client.patch(
        f"{api.BASE}/{session['id']}",
        json={"title": "Renamed", "expected_row_version": session["row_version"]},
        headers=api.headers,
    ).json()
    assert renamed["row_version"] == 2 and renamed["input_revision"] == 0
    archived = api.client.post(
        f"{api.BASE}/{session['id']}/archive",
        json={"expected_row_version": 2},
        headers=api.headers,
    ).json()
    assert archived["view"] == "ARCHIVED" and archived["input_revision"] == 0
    blocked = api.upload(archived, "a.txt", b"x")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SESSION_ARCHIVED"
    listed = api.client.get(api.BASE, headers=api.headers).json()["items"]
    assert session["id"] not in [i["id"] for i in listed]
    archived_list = api.client.get(
        f"{api.BASE}?archived=true", headers=api.headers
    ).json()
    assert session["id"] in [i["id"] for i in archived_list["items"]]
    restored = api.client.post(
        f"{api.BASE}/{session['id']}/restore",
        json={"expected_row_version": archived["row_version"]},
        headers=api.headers,
    )
    assert restored.json()["view"] == "DRAFT"


def test_ids_from_another_session_are_not_interchangeable(api, session):
    other = api.create("Other").json()
    uploaded = api.upload(session, "a.txt", b"mine").json()
    base = f"{api.BASE}/{other['id']}/sources/{uploaded['source_id']}"
    assert api.client.get(f"{base}/revisions", headers=api.headers).status_code == 404
    assert (
        _content(
            api, other["id"], uploaded["source_id"], uploaded["revision_id"]
        ).status_code
        == 404
    )
    assert api.include(other, uploaded["source_id"], False).status_code == 404
    run = api.prepare(uploaded["session"]).json()["run"]
    assert (
        api.client.get(
            f"{api.BASE}/{other['id']}/runs/{run['id']}", headers=api.headers
        ).status_code
        == 404
    )


def test_storage_unavailable_returns_503_and_no_partial_source(
    api, session, db_session, monkeypatch, tmp_path
):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("file")
    monkeypatch.setattr(
        settings, "onboarding_source_storage_root", str(blocker / "root")
    )
    response = api.upload(session, "a.txt", b"hello")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is True
    assert db_session.query(OnboardingSource).count() == 0


def test_filenames_are_display_metadata_not_paths(api, session, storage_root):
    uploaded = api.upload(session, "../../etc/passwd.txt", b"hello")
    assert uploaded.status_code == 201
    stored = [p for p in storage_root.rglob("*") if p.is_file()]
    assert stored and all("passwd" not in str(p) for p in stored)
    assert all(str(storage_root.resolve()) in str(p.resolve()) for p in stored)


def test_originals_are_not_world_accessible_directories(api, session, storage_root):
    api.upload(session, "a.txt", b"hello")
    if os.name != "nt":
        assert (storage_root.stat().st_mode & 0o077) == 0
