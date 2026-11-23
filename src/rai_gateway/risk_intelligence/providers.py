"""Content evaluator provider abstraction and adapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from rai_gateway.risk_intelligence.domain import (
    ContentRuleEvaluation,
    EvaluatorFailureType,
    StructuredEvaluatorOutput,
)


class ContentEvaluatorError(Exception):
    def __init__(self, failure_type: str, message: str):
        super().__init__(message)
        self.failure_type = failure_type
        self.message = message


@dataclass
class ProviderConfig:
    provider: str
    model: str
    temperature: float = 0.0
    timeout_seconds: int = 30
    max_rules_per_call: int = 20
    api_key: str | None = None
    base_url: str | None = None
    location: str | None = None
    gcp_project_id: str | None = None
    service_account_json: str | None = None


@dataclass
class EvaluatorRequest:
    content: str
    content_type: str
    applicable_rules: list[dict[str, Any]]
    ai_system_context: dict[str, Any] = field(default_factory=dict)
    agent_context: dict[str, Any] = field(default_factory=dict)


class ContentEvaluatorProvider(Protocol):
    name: str

    def evaluate(
        self, request: EvaluatorRequest, config: ProviderConfig
    ) -> StructuredEvaluatorOutput: ...


def _build_rules_prompt(rules: list[dict[str, Any]]) -> str:
    lines = []
    for rule in rules:
        lines.append(
            f"- rule_id={rule.get('id')} title={rule.get('title')!r} "
            f"content={rule.get('content')!r} effect={rule.get('effect')} "
            f"category={rule.get('category')}"
        )
    return "\n".join(lines)


EVALUATOR_SYSTEM_PROMPT = """You are a content policy evaluator for an AI governance system.
Evaluate the given content against each applicable Content Rule.
Return ONLY valid JSON matching this schema:
{
  "evaluations": [
    {
      "rule_id": "<uuid>",
      "violated": true|false,
      "confidence": 0.0-1.0,
      "category": "<string or null>",
      "reason": "<short explanation>",
      "evidence_spans": ["<exact substrings from content>"],
      "severity": "INFO|LOW|MEDIUM|HIGH|CRITICAL"
    }
  ]
}
Rules:
- Include exactly one evaluation object per applicable rule.
- Do not invent rule_ids.
- confidence must be between 0 and 1.
- If unsure, set violated=false with low confidence.
- Never decide governance effects (DENY/SUSPEND); only detect violations.
"""


def parse_structured_evaluator_output(
    raw: str | dict[str, Any],
    *,
    expected_rule_ids: set[str] | None = None,
) -> StructuredEvaluatorOutput:
    """Validate provider output into StructuredEvaluatorOutput."""
    if isinstance(raw, str):
        text = raw.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
                f"Invalid JSON from evaluator: {exc}",
            ) from exc
    else:
        data = raw

    if not isinstance(data, dict) or "evaluations" not in data:
        raise ContentEvaluatorError(
            EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
            "Missing evaluations array",
        )
    evaluations_raw = data["evaluations"]
    if not isinstance(evaluations_raw, list):
        raise ContentEvaluatorError(
            EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
            "evaluations must be a list",
        )

    evaluations: list[ContentRuleEvaluation] = []
    for item in evaluations_raw:
        if not isinstance(item, dict):
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
                "evaluation item must be an object",
            )
        rule_id = str(item.get("rule_id") or "")
        if not rule_id:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
                "evaluation missing rule_id",
            )
        if expected_rule_ids is not None and rule_id not in expected_rule_ids:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
                f"Unknown rule_id in evaluator output: {rule_id}",
            )
        confidence = item.get("confidence", 0.0)
        try:
            confidence_f = float(confidence)
        except (TypeError, ValueError) as exc:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
                f"Invalid confidence for rule {rule_id}",
            ) from exc
        if confidence_f < 0.0 or confidence_f > 1.0:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
                f"Confidence out of range for rule {rule_id}",
            )
        spans = item.get("evidence_spans") or []
        if not isinstance(spans, list):
            spans = []
        evaluations.append(
            ContentRuleEvaluation(
                rule_id=rule_id,
                violated=bool(item.get("violated")),
                confidence=confidence_f,
                category=item.get("category"),
                reason=item.get("reason"),
                evidence_spans=[str(s) for s in spans],
                severity=item.get("severity"),
            )
        )
    return StructuredEvaluatorOutput(evaluations=evaluations)


class MockContentEvaluator:
    """Deterministic evaluator for CI and local demos (no external API)."""

    name = "mock"

    # Simple keyword heuristics keyed by category/title/content fragments.
    _PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
        (
            re.compile(r"bank\s*account|EG\d{2}|IBAN|routing\s*number", re.I),
            "financial_data_disclosure",
            "CRITICAL",
        ),
        (
            re.compile(r"aws.?credentials|secret.?access.?key|AKIA[0-9A-Z]{16}", re.I),
            "credential_exposure",
            "CRITICAL",
        ),
        (
            re.compile(r"ssn|social\s*security|\b\d{3}-\d{2}-\d{4}\b", re.I),
            "pii_disclosure",
            "HIGH",
        ),
        (
            re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.I),
            "prompt_injection",
            "HIGH",
        ),
    ]

    def evaluate(
        self, request: EvaluatorRequest, config: ProviderConfig
    ) -> StructuredEvaluatorOutput:
        evaluations: list[ContentRuleEvaluation] = []
        content = request.content or ""
        for rule in request.applicable_rules[: config.max_rules_per_call]:
            rule_id = str(rule["id"])
            rule_text = f"{rule.get('title', '')} {rule.get('content', '')}".lower()
            violated = False
            confidence = 0.1
            category = rule.get("category")
            severity = rule.get("severity") or "MEDIUM"
            reason = "No violation detected"
            spans: list[str] = []

            for pattern, cat, sev in self._PATTERNS:
                match = pattern.search(content)
                if not match:
                    continue
                # Prefer rules whose text mentions the category/topic
                topic_hint = cat.replace("_", " ").split()[0]
                if (
                    topic_hint in rule_text
                    or "disclos" in rule_text
                    or "credential" in rule_text
                    or "inject" in rule_text
                    or True
                ):
                    # Match if rule looks related OR always apply pattern when rule
                    # content mentions bank/credential/pii/injection keywords.
                    related = any(
                        kw in rule_text
                        for kw in (
                            "bank",
                            "financial",
                            "credential",
                            "secret",
                            "pii",
                            "personal",
                            "inject",
                            "disclos",
                            "account",
                        )
                    ) or bool(rule.get("category"))
                    if related:
                        violated = True
                        confidence = 0.97
                        category = category or cat
                        severity = severity or sev
                        reason = f"Matched pattern for {cat}"
                        spans = [match.group(0)]
                        break

            evaluations.append(
                ContentRuleEvaluation(
                    rule_id=rule_id,
                    violated=violated,
                    confidence=confidence,
                    category=category,
                    reason=reason,
                    evidence_spans=spans,
                    severity=severity if violated else "INFO",
                )
            )
        return StructuredEvaluatorOutput(evaluations=evaluations)


class OpenAIContentEvaluator:
    """OpenAI Chat Completions adapter with strict JSON schema."""

    name = "openai"
    api_url = "https://api.openai.com/v1/chat/completions"
    default_model = "gpt-4o-mini"

    def evaluate(
        self, request: EvaluatorRequest, config: ProviderConfig
    ) -> StructuredEvaluatorOutput:
        return _openai_compatible_evaluate(
            request,
            config,
            provider_label="OpenAI",
            api_url=_chat_url(config.base_url) if config.base_url else self.api_url,
            default_model=self.default_model,
        )


class DeepSeekContentEvaluator:
    """DeepSeek Chat Completions adapter (OpenAI-compatible API)."""

    name = "deepseek"
    api_url = "https://api.deepseek.com/chat/completions"
    default_model = "deepseek-chat"

    def evaluate(
        self, request: EvaluatorRequest, config: ProviderConfig
    ) -> StructuredEvaluatorOutput:
        return _openai_compatible_evaluate(
            request,
            config,
            provider_label="DeepSeek",
            api_url=_chat_url(config.base_url) if config.base_url else self.api_url,
            default_model=self.default_model,
        )


def _chat_url(base: str) -> str:
    """Accept a base URL (.../v1) or a full chat-completions URL."""
    base = base.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


class OpenAICompatibleContentEvaluator:
    """Any OpenAI-compatible endpoint (Ollama, vLLM, LiteLLM, Groq, OpenRouter, Azure...)."""

    name = "openai_compatible"

    def evaluate(
        self, request: EvaluatorRequest, config: ProviderConfig
    ) -> StructuredEvaluatorOutput:
        if not config.base_url:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
                "openai_compatible evaluator needs CONTENT_EVALUATOR_BASE_URL",
            )
        return _openai_compatible_evaluate(
            request,
            config,
            provider_label="OpenAI-compatible",
            api_url=_chat_url(config.base_url),
            default_model=config.model or "default",
            allow_no_key=True,
        )


class VertexContentEvaluator:
    """Gemini on Vertex AI: a Google service account (default) or a Cloud API key."""

    name = "vertex"

    def evaluate(
        self, request: EvaluatorRequest, config: ProviderConfig
    ) -> StructuredEvaluatorOutput:
        import urllib.error
        import urllib.request

        from rai_gateway.core.config import settings
        from rai_gateway.provider_gateway.credentials.gcp_token import (
            GcpTokenError,
            gcp_token_provider,
        )

        project = config.gcp_project_id or settings.gcp_project_id
        use_api_key = bool(config.api_key)
        if not project and not use_api_key:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
                "vertex evaluator needs a GCP project (evaluator setting or GCP_PROJECT_ID)",
            )
        try:
            if use_api_key:
                token = config.api_key
            elif config.service_account_json:
                try:
                    info = json.loads(config.service_account_json)
                except ValueError as exc:
                    raise GcpTokenError("Evaluator service account JSON is invalid") from exc
                token = gcp_token_provider.get_token_for_info(info)
            else:
                token = gcp_token_provider.get_token()
        except GcpTokenError as exc:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
                f"Vertex credential unavailable: {exc}",
            ) from exc
        location = config.location or settings.content_evaluator_location or "global"
        host = (
            "aiplatform.googleapis.com"
            if location == "global"
            else f"{location}-aiplatform.googleapis.com"
        )
        model = config.model or "gemini-2.5-flash"
        if use_api_key:
            # Vertex AI express mode: a Google Cloud API key, no project or service account.
            url = (
                f"https://{host}/v1/publishers/google/models/{model}:generateContent"
            )
        else:
            url = (
                f"https://{host}/v1/projects/{project}/locations/{location}"
                f"/publishers/google/models/{model}:generateContent"
            )
        expected = {str(r["id"]) for r in request.applicable_rules}
        user_prompt = (
            f"Content type: {request.content_type}\n"
            f"Content:\n{request.content}\n\n"
            f"Applicable rules:\n{_build_rules_prompt(request.applicable_rules)}\n"
        )
        body = {
            "systemInstruction": {"parts": [{"text": EVALUATOR_SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": config.temperature,
                "responseMimeType": "application/json",
            },
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                **(
                    {"x-goog-api-key": str(token)}
                    if use_api_key
                    else {"Authorization": f"Bearer {token}"}
                ),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_TIMEOUT.value,
                "Vertex evaluator timed out",
            ) from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
                f"Vertex HTTP {exc.code}: {detail}",
            ) from exc
        except urllib.error.URLError as exc:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
                f"Vertex unavailable: {exc}",
            ) from exc
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            content = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
                "Unexpected Vertex response shape",
            ) from exc
        return parse_structured_evaluator_output(content, expected_rule_ids=expected)


def _openai_compatible_evaluate(
    request: EvaluatorRequest,
    config: ProviderConfig,
    *,
    provider_label: str,
    api_url: str,
    default_model: str,
    allow_no_key: bool = False,
) -> StructuredEvaluatorOutput:
    if not config.api_key and not allow_no_key:
        raise ContentEvaluatorError(
            EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
            f"{provider_label} API key not configured",
        )
    import urllib.error
    import urllib.request

    expected = {str(r["id"]) for r in request.applicable_rules}
    user_prompt = (
        f"Content type: {request.content_type}\n"
        f"Content:\n{request.content}\n\n"
        f"Applicable rules:\n{_build_rules_prompt(request.applicable_rules)}\n"
    )
    body = {
        "model": config.model or default_model,
        "temperature": config.temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": EVALUATOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            **({"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.timeout_seconds) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except TimeoutError as exc:
        raise ContentEvaluatorError(
            EvaluatorFailureType.EVALUATOR_TIMEOUT.value,
            f"{provider_label} evaluator timed out",
        ) from exc
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            detail = str(exc)
        raise ContentEvaluatorError(
            EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
            f"{provider_label} HTTP {exc.code}: {detail}",
        ) from exc
    except urllib.error.URLError as exc:
        raise ContentEvaluatorError(
            EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
            f"{provider_label} unavailable: {exc}",
        ) from exc

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ContentEvaluatorError(
            EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
            f"Unexpected {provider_label} response shape",
        ) from exc
    return parse_structured_evaluator_output(content, expected_rule_ids=expected)


class GeminiContentEvaluator:
    """Google Gemini generateContent adapter with JSON response mime type."""

    name = "gemini"

    def evaluate(
        self, request: EvaluatorRequest, config: ProviderConfig
    ) -> StructuredEvaluatorOutput:
        if not config.api_key:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
                "Gemini API key not configured",
            )
        import urllib.error
        import urllib.request

        expected = {str(r["id"]) for r in request.applicable_rules}
        user_prompt = (
            f"{EVALUATOR_SYSTEM_PROMPT}\n\n"
            f"Content type: {request.content_type}\n"
            f"Content:\n{request.content}\n\n"
            f"Applicable rules:\n{_build_rules_prompt(request.applicable_rules)}\n"
        )
        model = config.model or "gemini-1.5-flash"
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={config.api_key}"
        )
        body = {
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": config.temperature,
                "responseMimeType": "application/json",
            },
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_TIMEOUT.value,
                "Gemini evaluator timed out",
            ) from exc
        except urllib.error.URLError as exc:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
                f"Gemini unavailable: {exc}",
            ) from exc

        try:
            content = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE.value,
                "Unexpected Gemini response shape",
            ) from exc
        return parse_structured_evaluator_output(content, expected_rule_ids=expected)


class AnthropicContentEvaluator:
    """Claude via the Messages API, through the shared RAI_AI wire format."""

    name = "anthropic"

    def evaluate(
        self, request: EvaluatorRequest, config: ProviderConfig
    ) -> StructuredEvaluatorOutput:
        from rai_gateway.core import ai_provider

        if not config.api_key:
            raise ContentEvaluatorError(
                EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
                "Anthropic API key not configured",
            )
        expected = {str(r["id"]) for r in request.applicable_rules}
        user_prompt = (
            f"Content type: {request.content_type}\n"
            f"Content:\n{request.content}\n\n"
            f"Applicable rules:\n{_build_rules_prompt(request.applicable_rules)}\n"
        )
        cfg = ai_provider.AIConfig(
            provider="anthropic",
            model=config.model,
            models=(config.model,),
            api_key=config.api_key,
            base_url=config.base_url,
            gcp_project_id=None,
            location=None,
            service_account_json=None,
            timeout_seconds=float(config.timeout_seconds),
            temperature=config.temperature,
            max_output_tokens=4096,
            structured_mode="JSON_PROMPT",
            allow_private_network=False,
        )
        try:
            result = ai_provider.generate_json_text(
                cfg, system=EVALUATOR_SYSTEM_PROMPT, user=user_prompt
            )
        except ai_provider.AIProviderError as exc:
            failure = {
                "timeout": EvaluatorFailureType.EVALUATOR_TIMEOUT,
                "invalid_response": EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE,
                "truncated": EvaluatorFailureType.EVALUATOR_INVALID_RESPONSE,
            }.get(exc.kind, EvaluatorFailureType.EVALUATOR_UNAVAILABLE)
            raise ContentEvaluatorError(failure.value, exc.message) from exc
        return parse_structured_evaluator_output(result.text, expected_rule_ids=expected)


_PROVIDERS: dict[str, ContentEvaluatorProvider] = {
    "mock": MockContentEvaluator(),
    "openai": OpenAIContentEvaluator(),
    "deepseek": DeepSeekContentEvaluator(),
    "gemini": GeminiContentEvaluator(),
    "google_ai": GeminiContentEvaluator(),
    "openai_compatible": OpenAICompatibleContentEvaluator(),
    "vertex": VertexContentEvaluator(),
    "anthropic": AnthropicContentEvaluator(),
}


def get_provider(name: str) -> ContentEvaluatorProvider:
    from rai_gateway.core.ai_provider import normalize_provider

    provider = _PROVIDERS.get(name.lower()) or _PROVIDERS.get(normalize_provider(name))
    if not provider:
        raise ContentEvaluatorError(
            EvaluatorFailureType.EVALUATOR_UNAVAILABLE.value,
            f"Unknown content evaluator provider: {name}",
        )
    return provider


def register_provider(name: str, provider: ContentEvaluatorProvider) -> None:
    """Test/extension hook to register alternate providers."""
    _PROVIDERS[name.lower()] = provider
