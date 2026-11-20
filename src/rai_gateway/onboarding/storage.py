"""Private original-source storage for onboarding intake (Step 02).

A small adapter interface plus a local filesystem implementation. Keys are
server-generated and opaque; user filenames are display metadata only. Nothing
here parses document text or runs workbook formulas/macros.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, Protocol

_KEY_RE = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{32}$")
_HEAD_BYTES = 8192
_MAX_ZIP_MEMBERS = 20_000

SUPPORTED_FAMILIES = ("PDF", "DOCX", "XLSX", "CSV", "TXT", "MARKDOWN")
_EXT_FAMILY = {
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".xlsx": "XLSX",
    ".csv": "CSV",
    ".txt": "TXT",
    ".md": "MARKDOWN",
    ".markdown": "MARKDOWN",
}
_GENERIC_MIME = {"", "application/octet-stream", "binary/octet-stream"}
_FAMILY_MIME = {
    "PDF": {"application/pdf", "application/x-pdf"},
    "DOCX": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/x-zip-compressed",
    },
    "XLSX": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/x-zip-compressed",
    },
    "CSV": {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"},
    "TXT": {"text/plain"},
    "MARKDOWN": {"text/markdown", "text/x-markdown", "text/plain"},
}
FAMILY_CONTENT_TYPE = {
    "PDF": "application/pdf",
    "DOCX": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "CSV": "text/csv",
    "TXT": "text/plain",
    "MARKDOWN": "text/markdown",
    "TEXT": "text/plain",
}


class SourceStorageError(Exception):
    """Storage is unavailable or an object could not be read/written."""


class SourceLimitError(Exception):
    """Streamed input exceeded the byte or time limit."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class UnsupportedMedia(Exception):
    pass


@dataclass
class StagedObject:
    path: Path
    sha256: str
    byte_length: int
    head: bytes


class OnboardingSourceStore(Protocol):
    def stage(
        self,
        chunks: Iterable[bytes],
        *,
        max_bytes: int,
        deadline: float | None = None,
    ) -> StagedObject: ...

    def finalize(self, staged: StagedObject) -> str: ...

    def discard(self, staged: StagedObject) -> None: ...

    def open_read(self, key: str) -> BinaryIO: ...

    def verify(self, key: str, *, sha256: str, byte_length: int) -> None: ...

    def local_path(self, key: str) -> Path: ...

    def check_writable(self) -> None: ...

    def cleanup_orphans(
        self,
        *,
        is_referenced: Callable[[str], bool],
        min_age_seconds: float,
        limit: int = 500,
    ) -> dict[str, int]: ...


def detect_media_family(
    filename: str,
    declared_type: str | None,
    staged: StagedObject,
) -> str:
    """Identify the accepted media family from extension, MIME and bounded bytes."""
    ext = os.path.splitext(filename or "")[1].lower()
    family = _EXT_FAMILY.get(ext)
    if family is None:
        raise UnsupportedMedia("File type is not supported")
    declared = (declared_type or "").split(";")[0].strip().lower()
    if declared not in _GENERIC_MIME and declared not in _FAMILY_MIME[family]:
        raise UnsupportedMedia("Declared content type does not match the file type")
    head = staged.head
    if staged.byte_length == 0:
        raise UnsupportedMedia("File is empty")
    if family == "PDF":
        if not head.lstrip(b"\x00 \t\r\n").startswith(b"%PDF-"):
            raise UnsupportedMedia("File content is not a PDF")
    elif family in ("DOCX", "XLSX"):
        _check_ooxml(staged.path, family)
    else:
        _check_text_like(head)
    return family


def _check_ooxml(path: Path, family: str) -> None:
    required = "word/document.xml" if family == "DOCX" else "xl/workbook.xml"
    try:
        with zipfile.ZipFile(path) as archive:  # reads the central directory only
            infos = archive.infolist()
            if len(infos) > _MAX_ZIP_MEMBERS:
                raise UnsupportedMedia("Document container is too large")
            names = {info.filename for info in infos}
    except zipfile.BadZipFile as exc:
        raise UnsupportedMedia("File content is not an Office document") from exc
    if "[Content_Types].xml" not in names or required not in names:
        raise UnsupportedMedia("File content is not the expected Office document")


def _check_text_like(head: bytes) -> None:
    if b"\x00" in head:
        raise UnsupportedMedia("File content is not plain text")
    for magic in (b"%PDF-", b"PK\x03\x04", b"MZ", b"\x7fELF", b"\xd0\xcf\x11\xe0"):
        if head.startswith(magic):
            raise UnsupportedMedia("File content is not plain text")
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte sequence may be cut at the head boundary.
        if exc.start < len(head) - 4:
            raise UnsupportedMedia("File content is not UTF-8 text") from exc


class FilesystemSourceStore:
    """Private filesystem adapter. API and worker must share the same root."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root).resolve()
        self._staging = self._root / "staging"
        self._objects = self._root / "objects"

    # -- helpers ------------------------------------------------------------
    def _ensure_dirs(self) -> None:
        for directory in (self._root, self._staging, self._objects):
            try:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            except OSError as exc:
                raise SourceStorageError("Source storage is unavailable") from exc

    def _path_for(self, key: str) -> Path:
        if not _KEY_RE.match(key or ""):
            raise SourceStorageError("Invalid storage key")
        path = self._objects / key
        # Reject any symlink component and enforce root containment.
        for candidate in (path.parent, path):
            if candidate.is_symlink():
                raise SourceStorageError("Storage path escapes the source root")
        resolved = path.resolve()
        if self._objects.resolve() not in resolved.parents:
            raise SourceStorageError("Storage path escapes the source root")
        return path

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        if os.name == "nt":
            return
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    # -- interface ----------------------------------------------------------
    def check_writable(self) -> None:
        self._ensure_dirs()
        probe = self._staging / f"probe-{uuid.uuid4().hex}.tmp"
        try:
            with open(probe, "wb") as handle:
                handle.write(b"ok")
                handle.flush()
                os.fsync(handle.fileno())
            probe.unlink()
        except OSError as exc:
            raise SourceStorageError("Source storage is not writable") from exc

    def stage(
        self,
        chunks: Iterable[bytes],
        *,
        max_bytes: int,
        deadline: float | None = None,
    ) -> StagedObject:
        self._ensure_dirs()
        path = self._staging / f"{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        total = 0
        head = bytearray()
        try:
            with open(path, "xb") as handle:
                for chunk in chunks:
                    if not chunk:
                        continue
                    if deadline is not None and time.monotonic() > deadline:
                        raise SourceLimitError("UPLOAD_TIMEOUT")
                    total += len(chunk)
                    if total > max_bytes:
                        raise SourceLimitError("SOURCE_TOO_LARGE")
                    digest.update(chunk)
                    if len(head) < _HEAD_BYTES:
                        head.extend(chunk[: _HEAD_BYTES - len(head)])
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            self._unlink(path)
            raise SourceStorageError("Source storage is unavailable") from exc
        except BaseException:
            self._unlink(path)
            raise
        return StagedObject(path, digest.hexdigest(), total, bytes(head))

    def finalize(self, staged: StagedObject) -> str:
        """Publish atomically under a fresh opaque key; never overwrite."""
        self._ensure_dirs()
        for _ in range(5):
            token = uuid.uuid4().hex
            key = f"{token[:2]}/{token}"
            final = self._path_for(key)
            try:
                final.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                try:
                    os.link(staged.path, final)  # fails if it exists
                except FileExistsError:
                    continue
                except (OSError, NotImplementedError):
                    if final.exists():
                        continue
                    os.replace(staged.path, final)
                else:
                    staged.path.unlink(missing_ok=True)
                self._fsync_dir(final.parent)
                return key
            except OSError as exc:
                raise SourceStorageError("Source storage is unavailable") from exc
        raise SourceStorageError("Could not allocate a storage key")

    def discard(self, staged: StagedObject) -> None:
        self._unlink(staged.path)

    def discard_key(self, key: str) -> None:
        try:
            self._unlink(self._path_for(key))
        except SourceStorageError:
            pass

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def open_read(self, key: str) -> BinaryIO:
        path = self._path_for(key)
        try:
            return open(path, "rb")
        except FileNotFoundError as exc:
            raise SourceStorageError("Original is missing") from exc
        except OSError as exc:
            raise SourceStorageError("Source storage is unavailable") from exc

    def local_path(self, key: str) -> Path:
        """Contained, symlink-free path of an original (filesystem adapter only)."""
        path = self._path_for(key)
        if not path.is_file():
            raise SourceStorageError("Original is missing")
        return path

    def verify(self, key: str, *, sha256: str, byte_length: int) -> None:
        """Check existence, exact size and digest. Raises SourceStorageError."""
        digest = hashlib.sha256()
        size = 0
        with self.open_read(key) as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > byte_length:
                    break
                digest.update(block)
        if size != byte_length or digest.hexdigest() != sha256:
            raise SourceIntegrityError("Original does not match its saved digest")

    def cleanup_orphans(
        self,
        *,
        is_referenced: Callable[[str], bool],
        min_age_seconds: float,
        limit: int = 500,
    ) -> dict[str, int]:
        """Remove only stale staging files and unreferenced finalized objects.

        ``is_referenced`` is consulted immediately before each deletion.
        """
        stats = {"staging_removed": 0, "objects_removed": 0, "skipped": 0}
        cutoff = time.time() - min_age_seconds
        budget = limit
        if self._staging.is_dir():
            for entry in self._staging.iterdir():
                if budget <= 0:
                    break
                if entry.is_symlink() or not entry.is_file():
                    continue
                if entry.stat().st_mtime < cutoff:
                    self._unlink(entry)
                    stats["staging_removed"] += 1
                    budget -= 1
        if self._objects.is_dir():
            for shard in self._objects.iterdir():
                if budget <= 0:
                    break
                if shard.is_symlink() or not shard.is_dir():
                    continue
                for entry in shard.iterdir():
                    if budget <= 0:
                        break
                    if entry.is_symlink() or not entry.is_file():
                        continue
                    key = f"{shard.name}/{entry.name}"
                    if not _KEY_RE.match(key):
                        continue
                    if entry.stat().st_mtime >= cutoff:
                        continue
                    if is_referenced(key):
                        stats["skipped"] += 1
                        continue
                    self._unlink(entry)
                    stats["objects_removed"] += 1
                    budget -= 1
        return stats


class SourceIntegrityError(SourceStorageError):
    """Original exists but its size/digest no longer matches."""


def get_source_store() -> FilesystemSourceStore:
    from rai_gateway.core.config import settings

    return FilesystemSourceStore(settings.onboarding_source_storage_root)
