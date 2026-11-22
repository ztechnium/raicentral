"""Parser profile: what exactly processes a document. Part of extraction identity."""

from __future__ import annotations

from importlib import metadata
from typing import Any

from .contracts import (
    ALLOWED_DELIMITERS,
    ALLOWED_ENCODINGS,
    ALLOWED_HEADER_MODES,
    EXTRACTION_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
    PARSER_BUILD_VERSION,
    Limits,
    digest_of,
)

DEPENDENCIES = ("pypdf", "python-docx", "openpyxl", "defusedxml")


def dependency_versions() -> dict[str, str]:
    out = {}
    for name in DEPENDENCIES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = "missing"
    return out


def parser_profile(limits: Limits) -> dict[str, Any]:
    profile = {
        "build": PARSER_BUILD_VERSION,
        "schema_version": EXTRACTION_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "dependencies": dependency_versions(),
        "limits": limits.to_dict(),
        "limits_digest": limits.digest(),
    }
    profile["fingerprint"] = digest_of(
        {k: v for k, v in profile.items() if k != "limits"}
    )
    return profile


def supported_options() -> dict[str, Any]:
    return {
        "encodings": list(ALLOWED_ENCODINGS),
        "delimiters": list(ALLOWED_DELIMITERS),
        "header_modes": list(ALLOWED_HEADER_MODES),
    }
