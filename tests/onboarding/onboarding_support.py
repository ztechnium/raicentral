"""Helpers shared by the onboarding tests (unique module name: several conftest.py files
exist in this repository, so tests must never ``from conftest import``)."""

from __future__ import annotations

import io
import uuid
import zipfile

from fastapi.testclient import TestClient

PURE_MODULES = {"test_parsers"}
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


DOCX = make_zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"})
XLSX = make_zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<w/>"})
CSV = b"system,owner\nchatbot,alice\n"
MD = "# Policy\n\nUse AI responsibly — é ü\n".encode("utf-8")


class Intake:
    """Tiny typed wrapper over the HTTP contract."""

    BASE = "/v1/productization/onboarding/sessions"

    def __init__(self, client: TestClient, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers

    def _h(self, key: str | None = None) -> dict[str, str]:
        return {**self.headers, "Idempotency-Key": key or uuid.uuid4().hex}

    def create(self, title: str = "Q4 onboarding", key: str | None = None):
        return self.client.post(self.BASE, json={"title": title}, headers=self._h(key))

    def get(self, session_id: str):
        return self.client.get(f"{self.BASE}/{session_id}", headers=self.headers)

    def upload(
        self,
        session: dict,
        name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        key: str | None = None,
        version: int | None = None,
    ):
        return self.client.post(
            f"{self.BASE}/{session['id']}/sources/files",
            files={"file": (name, data, content_type)},
            data={"expected_row_version": str(version or session["row_version"])},
            headers=self._h(key),
        )

    def replace_file(self, session, source_id, name, data, key=None, version=None):
        return self.client.post(
            f"{self.BASE}/{session['id']}/sources/{source_id}/revisions/file",
            files={"file": (name, data, "application/octet-stream")},
            data={"expected_row_version": str(version or session["row_version"])},
            headers=self._h(key),
        )

    def text(
        self,
        session,
        text_value,
        role="USER_PROMPT",
        title=None,
        key=None,
        version=None,
    ):
        return self.client.post(
            f"{self.BASE}/{session['id']}/sources/text",
            json={
                "role": role,
                "text": text_value,
                "title": title,
                "expected_row_version": version or session["row_version"],
            },
            headers=self._h(key),
        )

    def revise_text(self, session, source_id, text_value, key=None, version=None):
        return self.client.post(
            f"{self.BASE}/{session['id']}/sources/{source_id}/revisions/text",
            json={
                "text": text_value,
                "expected_row_version": version or session["row_version"],
            },
            headers=self._h(key),
        )

    def include(self, session, source_id, included: bool, version=None):
        return self.client.patch(
            f"{self.BASE}/{session['id']}/sources/{source_id}",
            json={
                "included": included,
                "expected_row_version": version or session["row_version"],
            },
            headers=self.headers,
        )

    def prepare(self, session, key=None, version=None, revision=None):
        return self.client.post(
            f"{self.BASE}/{session['id']}/prepare",
            json={
                "expected_row_version": version or session["row_version"],
                "requested_input_revision": (
                    session["input_revision"] if revision is None else revision
                ),
            },
            headers=self._h(key),
        )

    def retry(self, session, run_id, key=None, version=None, revision=None):
        return self.client.post(
            f"{self.BASE}/{session['id']}/runs/{run_id}/retry",
            json={
                "expected_row_version": version or session["row_version"],
                "requested_input_revision": (
                    session["input_revision"] if revision is None else revision
                ),
            },
            headers=self._h(key),
        )

    def cancel(self, session_id, run_id):
        return self.client.post(
            f"{self.BASE}/{session_id}/runs/{run_id}/cancel", headers=self.headers
        )


def fresh(api: Intake, session: dict) -> dict:
    """Reload the session from the server (it is the only authority)."""
    response = api.get(session["id"])
    assert response.status_code == 200, response.text
    return response.json()
