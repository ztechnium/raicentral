"""Bounded OOXML (DOCX/XLSX) preflight. Never unpacks to disk."""

from __future__ import annotations

import posixpath
import zipfile
from typing import Any

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from .contracts import FAILED, UNSUPPORTED, result


class OoxmlRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def read_bounded(archive: zipfile.ZipFile, name: str, cap: int) -> bytes:
    with archive.open(name) as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise OoxmlRejected("ARCHIVE_LIMIT_EXCEEDED")
    return data


def preflight(path: str, limits: Any, parts_to_parse: tuple[str, ...]) -> set[str]:
    """Check entry count, expansion size/ratio, path safety; parse key XML safely.

    Returns the member names so callers can detect unsupported parts.
    """
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        with open(path, "rb") as handle:
            magic = handle.read(8)
        # Password-protected OOXML is an OLE compound file, not a ZIP.
        if magic.startswith(bytes([0xD0, 0xCF, 0x11, 0xE0])):
            raise OoxmlRejected("ENCRYPTED_DOCUMENT") from exc
        raise OoxmlRejected("MALFORMED_DOCUMENT") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > limits.zip_entries:
            raise OoxmlRejected("ARCHIVE_LIMIT_EXCEEDED")
        total = 0
        for info in infos:
            name = info.filename
            parts = name.replace("\\", "/").split("/")
            if name.startswith(("/", "\\")) or ".." in parts or posixpath.isabs(name):
                raise OoxmlRejected("UNSAFE_ARCHIVE")
            if info.flag_bits & 0x1:
                raise OoxmlRejected("ENCRYPTED_DOCUMENT")
            total += info.file_size
            if (
                info.file_size > limits.zip_member_bytes
                or total > limits.zip_total_bytes
            ):
                raise OoxmlRejected("ARCHIVE_LIMIT_EXCEEDED")
            if (
                info.compress_size > 0
                and info.file_size / info.compress_size > limits.zip_ratio
            ):
                raise OoxmlRejected("ARCHIVE_LIMIT_EXCEEDED")
        names = {i.filename for i in infos}
        for part in parts_to_parse:
            if part not in names:
                continue
            try:
                SafeET.fromstring(read_bounded(archive, part, limits.zip_member_bytes))
            except OoxmlRejected:
                raise
            except DefusedXmlException as exc:
                raise OoxmlRejected("UNSAFE_XML") from exc
            except Exception as exc:  # noqa: BLE001 - malformed XML
                raise OoxmlRejected("MALFORMED_DOCUMENT") from exc
        return names


def rejected_result(code: str) -> dict[str, Any]:
    if code == "ENCRYPTED_DOCUMENT":
        return result(UNSUPPORTED, reason="PASSWORD_REQUIRED")
    return result(FAILED, reason=code)
