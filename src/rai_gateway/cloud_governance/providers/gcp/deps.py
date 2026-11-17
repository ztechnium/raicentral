"""Optional Google Cloud SDK availability."""

from __future__ import annotations


class GcpDependencyError(RuntimeError):
    pass


def ensure_google_auth() -> None:
    try:
        import google.auth  # noqa: F401
    except ImportError as exc:
        raise GcpDependencyError(
            "Google Cloud libraries are not installed. "
            "Install with: pip install rai-gateway[gcp]"
        ) from exc


def gcp_libraries_available() -> bool:
    try:
        import google.auth  # noqa: F401

        return True
    except ImportError:
        return False
