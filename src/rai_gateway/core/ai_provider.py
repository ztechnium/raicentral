"""One environment-only AI configuration for every internal AI feature.

``RAI_AI_*`` selects a provider, a model and a credential once. Content evaluation,
AI onboarding and any later internal AI feature read it through this module instead
of growing a per-feature provider setup. Nothing here is stored in the database and
nothing is editable from the UI.

Providers: openai, anthropic, gemini (Google AI Studio, API key), vertex (Vertex AI,
API key *or* service account) and openai_compatible (any OpenAI-style endpoint).

The module has three layers, all pure except :func:`generate_json_text`:

* :func:`load_ai_config` - settings -> a validated, immutable :class:`AIConfig`.
* ``api_base`` / ``request_path`` / ``auth_headers`` / ``build_body`` /
  ``extract_response`` - the provider wire formats. The onboarding probe reuses these
  behind its own destination-pinning transport.
* :func:`generate_json_text` - a plain HTTPS call for internal features.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from rai_gateway.core.config import settings

PROVIDERS = ("openai", "anthropic", "gemini", "vertex", "openai_compatible")
_ALIASES = {
    "google_ai": "gemini",
    "google": "gemini",
    "vertex_ai": "vertex",
    "claude": "anthropic",
    "openai-compatible": "openai_compatible",
    "openai_compat": "openai_compatible",
}

STRUCTURED_MODES = ("JSON_SCHEMA", "JSON_OBJECT", "JSON_PROMPT")
DEFAULT_STRUCTURED_MODE = {
    "openai": "JSON_SCHEMA",
    "openai_compatible": "JSON_OBJECT",
    "anthropic": "JSON_PROMPT",
    "gemini": "JSON_OBJECT",
    "vertex": "JSON_OBJECT",
}
# JSON_SCHEMA is only sent where the wire format takes a strict OpenAI-style schema.
_MODES_BY_PROVIDER = {
    "openai": ("JSON_SCHEMA", "JSON_OBJECT", "JSON_PROMPT"),
    "openai_compatible": ("JSON_SCHEMA", "JSON_OBJECT", "JSON_PROMPT"),
    "anthropic": ("JSON_PROMPT",),
    "gemini": ("JSON_OBJECT", "JSON_PROMPT"),
    "vertex": ("JSON_OBJECT", "JSON_PROMPT"),
}
_DEFAULT_BASE = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}
ANTHROPIC_VERSION = "2023-06-01"

OPENAI_FAMILY = ("openai", "openai_compatible")
GOOGLE_FAMILY = ("gemini", "vertex")


class AIConfigError(ValueError):
    """The RAI_AI_* settings are incomplete or inconsistent."""


class AIProviderError(Exception):
    """A provider call failed. ``kind`` is a stable, secret-free category."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class AIConfig:
    provider: str
    model: str
    models: tuple[str, ...]
    api_key: str | None
    base_url: str | None
    gcp_project_id: str | None
    location: str | None
    service_account_json: str | None
    timeout_seconds: float
    temperature: float
    max_output_tokens: int
    structured_mode: str
    allow_private_network: bool

    @property
    def auth(self) -> str:
        """``api_key`` | ``service_account`` (Vertex only) | ``none``."""
        if self.api_key:
            return "api_key"
        if self.provider == "vertex":
            return "service_account"
        return "none"

    @property
    def token_param(self) -> str:
        return "max_completion_tokens" if self.provider == "openai" else "max_tokens"


def normalize_provider(name: str | None) -> str:
    key = (name or "").strip().lower()
    return _ALIASES.get(key, key)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def load_ai_config() -> AIConfig | None:
    """The configured provider, ``None`` when RAI_AI_PROVIDER is unset.

    Raises :class:`AIConfigError` when it is set but unusable, so a typo is reported
    instead of silently disabling AI.
    """
    provider = normalize_provider(settings.rai_ai_provider)
    if not provider:
        return None
    if provider not in PROVIDERS:
        raise AIConfigError(
            f"RAI_AI_PROVIDER must be one of {', '.join(PROVIDERS)} (got {provider!r})"
        )
    model = _clean(settings.rai_ai_model)
    if not model:
        raise AIConfigError("RAI_AI_MODEL is required")
    extra = tuple(
        m for m in (s.strip() for s in (settings.rai_ai_models or "").split(",")) if m
    )
    api_key = _clean(settings.rai_ai_api_key)
    base_url = _clean(settings.rai_ai_base_url)
    if provider == "openai_compatible" and not base_url:
        raise AIConfigError("RAI_AI_BASE_URL is required for openai_compatible")
    if provider in ("openai", "anthropic", "gemini") and not api_key:
        raise AIConfigError(f"RAI_AI_API_KEY is required for {provider}")
    service_account = _clean(settings.rai_ai_gcp_service_account_json)
    if service_account:
        try:
            info = json.loads(service_account)
        except ValueError as exc:
            raise AIConfigError("RAI_AI_GCP_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
        if not isinstance(info, dict) or not info.get("client_email") or not info.get(
            "private_key"
        ):
            raise AIConfigError(
                "RAI_AI_GCP_SERVICE_ACCOUNT_JSON needs client_email and private_key"
            )
    project = _clean(settings.rai_ai_gcp_project_id) or _clean(settings.gcp_project_id)
    location = _clean(settings.rai_ai_location) or _clean(settings.gcp_location)
    if provider == "vertex" and not api_key and not project:
        raise AIConfigError(
            "Vertex with a service account needs RAI_AI_GCP_PROJECT_ID or GCP_PROJECT_ID"
        )
    mode = _clean(settings.rai_ai_structured_mode) or DEFAULT_STRUCTURED_MODE[provider]
    mode = mode.upper()
    if mode not in _MODES_BY_PROVIDER[provider]:
        raise AIConfigError(
            f"RAI_AI_STRUCTURED_MODE {mode!r} is not supported for {provider}"
        )
    return AIConfig(
        provider=provider,
        model=model,
        models=tuple(dict.fromkeys((model, *extra))),
        api_key=api_key,
        base_url=base_url,
        gcp_project_id=project,
        location=location or "global",
        service_account_json=service_account,
        timeout_seconds=float(settings.rai_ai_timeout_seconds),
        temperature=float(settings.rai_ai_temperature),
        max_output_tokens=int(settings.rai_ai_max_output_tokens),
        structured_mode=mode,
        allow_private_network=bool(settings.rai_ai_allow_private_network),
    )


def try_load_ai_config() -> tuple[AIConfig | None, str | None]:
    """``(config, None)``, ``(None, None)`` when unset, or ``(None, problem)``."""
    try:
        return load_ai_config(), None
    except AIConfigError as exc:
        return None, str(exc)


# ----------------------------------------------------------------- wire formats
def api_base(
    provider: str,
    *,
    base_url: str | None = None,
    location: str | None = None,
) -> str:
    """Scheme + host (+ fixed prefix) that every request path is appended to."""
    if provider == "vertex":
        loc = location or "global"
        host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
        return f"https://{host}"
    if provider == "openai_compatible":
        return (base_url or "").rstrip("/")
    return (base_url or _DEFAULT_BASE[provider]).rstrip("/")


def request_path(
    provider: str,
    model: str,
    *,
    auth: str,
    project: str | None = None,
    location: str | None = None,
) -> str:
    safe = quote(model, safe="-._~")
    if provider in OPENAI_FAMILY:
        return "/chat/completions"
    if provider == "anthropic":
        return "/messages"
    if provider == "gemini":
        return f"/models/{safe}:generateContent"
    # vertex
    if auth == "api_key":
        return f"/v1/publishers/google/models/{safe}:generateContent"
    return (
        f"/v1/projects/{quote(project or '', safe='-._~')}"
        f"/locations/{quote(location or 'global', safe='-._~')}"
        f"/publishers/google/models/{safe}:generateContent"
    )


def auth_headers(provider: str, auth: str, secret: str | None) -> dict[str, str]:
    if not secret:
        return {}
    if provider in OPENAI_FAMILY:
        return {"Authorization": f"Bearer {secret}"}
    if provider == "anthropic":
        return {"x-api-key": secret, "anthropic-version": ANTHROPIC_VERSION}
    if provider == "gemini" or (provider == "vertex" and auth == "api_key"):
        return {"x-goog-api-key": secret}
    return {"Authorization": f"Bearer {secret}"}  # vertex service-account token


def build_body(
    provider: str,
    *,
    model: str,
    system: str,
    user: str,
    max_output_tokens: int,
    structured_mode: str,
    token_param: str = "max_tokens",
    temperature: float | None = None,
    schema_name: str = "structured_output",
    schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if structured_mode not in STRUCTURED_MODES:
        raise AIProviderError("request_rejected", "Unsupported structured-output mode")
    if provider in OPENAI_FAMILY:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            token_param: int(max_output_tokens),
            "stream": False,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if structured_mode == "JSON_SCHEMA":
            if schema is None:
                raise AIProviderError("request_rejected", "JSON_SCHEMA needs a schema")
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": dict(schema)},
            }
        elif structured_mode == "JSON_OBJECT":
            body["response_format"] = {"type": "json_object"}
        return body
    if provider == "anthropic":
        # Claude has no JSON-mode switch: the prompt asks for JSON and the caller
        # validates the result. Temperature is omitted (newer models reject it).
        return {
            "model": model,
            "max_tokens": int(max_output_tokens),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
    generation: dict[str, Any] = {"maxOutputTokens": int(max_output_tokens)}
    if temperature is not None:
        generation["temperature"] = temperature
    if structured_mode in ("JSON_OBJECT", "JSON_SCHEMA"):
        generation["responseMimeType"] = "application/json"
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": generation,
    }


@dataclass(frozen=True)
class Extracted:
    text: str | None
    truncated: bool
    refused: bool
    usage: dict[str, int] | None


def _int_usage(src: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, int] | None:
    out = {
        dest: int(src[name])
        for dest, name in names.items()
        if isinstance(src.get(name), int) and not isinstance(src.get(name), bool)
    }
    if out and "total_tokens" not in out and {"prompt_tokens", "completion_tokens"} <= set(out):
        out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
    return out or None  # missing usage is unknown, never zero


def extract_response(provider: str, payload: Any) -> Extracted:
    """Normalise a provider response. Raises :class:`AIProviderError` on a bad shape."""
    try:
        if provider in OPENAI_FAMILY:
            choice = payload["choices"][0]
            message = choice["message"]
            usage = payload.get("usage")
            content = message.get("content")
            return Extracted(
                text=content if isinstance(content, str) else None,
                truncated=choice.get("finish_reason") == "length",
                refused=bool(message.get("refusal")),
                usage=_int_usage(
                    usage if isinstance(usage, dict) else {},
                    {
                        "prompt_tokens": "prompt_tokens",
                        "completion_tokens": "completion_tokens",
                        "total_tokens": "total_tokens",
                    },
                ),
            )
        if provider == "anthropic":
            blocks = payload["content"]
            text = "".join(
                b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
            )
            usage = payload.get("usage")
            return Extracted(
                text=text or None,
                truncated=payload.get("stop_reason") == "max_tokens",
                refused=payload.get("stop_reason") == "refusal",
                usage=_int_usage(
                    usage if isinstance(usage, dict) else {},
                    {"prompt_tokens": "input_tokens", "completion_tokens": "output_tokens"},
                ),
            )
        feedback = payload.get("promptFeedback") or {}
        usage = payload.get("usageMetadata")
        usage_out = _int_usage(
            usage if isinstance(usage, dict) else {},
            {
                "prompt_tokens": "promptTokenCount",
                "completion_tokens": "candidatesTokenCount",
                "total_tokens": "totalTokenCount",
            },
        )
        if feedback.get("blockReason"):
            return Extracted(None, False, True, usage_out)
        candidate = payload["candidates"][0]
        finish = candidate.get("finishReason")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(
            p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")
        )
        return Extracted(
            text=text or None,
            truncated=finish == "MAX_TOKENS",
            refused=finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"),
            usage=usage_out,
        )
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise AIProviderError("invalid_response", f"Unexpected {provider} response shape") from exc


# ------------------------------------------------------------------ credentials
def resolve_credential(cfg: AIConfig) -> str | None:
    """API key, or a short-lived Google token for Vertex with a service account."""
    if cfg.auth != "service_account":
        return cfg.api_key
    from rai_gateway.provider_gateway.credentials.gcp_token import (
        GcpTokenError,
        gcp_token_provider,
    )

    try:
        if cfg.service_account_json:
            return gcp_token_provider.get_token_for_info(json.loads(cfg.service_account_json))
        return gcp_token_provider.get_token()
    except (GcpTokenError, ValueError) as exc:
        raise AIProviderError("credential_unavailable", f"Vertex credential unavailable: {exc}") from exc


# --------------------------------------------------------------- generic caller
@dataclass(frozen=True)
class GenerationResult:
    text: str
    usage: dict[str, int] | None


def generate_json_text(
    cfg: AIConfig,
    *,
    system: str,
    user: str,
    max_output_tokens: int | None = None,
    structured_mode: str | None = None,
    schema: Mapping[str, Any] | None = None,
    temperature: float | None = None,
    model: str | None = None,
    timeout_seconds: float | None = None,
) -> GenerationResult:
    """One non-streaming request. Returns the model text; the caller parses it."""
    mode = structured_mode or cfg.structured_mode
    if mode == "JSON_SCHEMA" and schema is None:
        mode = "JSON_OBJECT"
    chosen_model = model or cfg.model
    base = api_base(cfg.provider, base_url=cfg.base_url, location=cfg.location)
    url = base + request_path(
        cfg.provider,
        chosen_model,
        auth=cfg.auth,
        project=cfg.gcp_project_id,
        location=cfg.location,
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **auth_headers(cfg.provider, cfg.auth, resolve_credential(cfg)),
    }
    body = build_body(
        cfg.provider,
        model=chosen_model,
        system=system,
        user=user,
        max_output_tokens=max_output_tokens or cfg.max_output_tokens,
        structured_mode=mode,
        token_param=cfg.token_param,
        temperature=cfg.temperature if temperature is None else temperature,
        schema=schema,
    )
    timeout = timeout_seconds or cfg.timeout_seconds
    try:
        response = httpx.post(
            url,
            headers=headers,
            content=json.dumps(body).encode("utf-8"),
            timeout=timeout,
            follow_redirects=False,
        )
    except httpx.TimeoutException as exc:
        raise AIProviderError("timeout", f"{cfg.provider} request timed out") from exc
    except httpx.HTTPError as exc:
        raise AIProviderError("unavailable", f"{cfg.provider} unavailable: {type(exc).__name__}") from exc
    if response.status_code in (401, 403):
        raise AIProviderError("auth", f"{cfg.provider} rejected the credential (HTTP {response.status_code})")
    if response.status_code >= 400:
        raise AIProviderError(
            "unavailable", f"{cfg.provider} HTTP {response.status_code}: {response.text[:300]}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise AIProviderError("invalid_response", f"{cfg.provider} returned non-JSON") from exc
    extracted = extract_response(cfg.provider, payload)
    if extracted.refused:
        raise AIProviderError("refusal", f"{cfg.provider} refused the request")
    if extracted.truncated:
        raise AIProviderError("truncated", f"{cfg.provider} output was truncated")
    if not extracted.text or not extracted.text.strip():
        raise AIProviderError("invalid_response", f"{cfg.provider} returned no text")
    return GenerationResult(text=extracted.text, usage=extracted.usage)
