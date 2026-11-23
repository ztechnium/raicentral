"""HTTP surface of the assistant: ``/v1/productization/assistant``.

Both routes call the management API *as the caller* (their own bearer token, through the
in-process app), so every existing permission check still decides what is allowed.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from rai_gateway.assistant import engine
from rai_gateway.assistant.catalog import Tool
from rai_gateway.core import ai_provider
from rai_gateway.core.authorization import require_tenant
from rai_gateway.core.tenant_context import TenantContext

router = APIRouter(prefix="/v1/productization/assistant", tags=["assistant"])

_BASE_URL = "http://assistant.internal"
_REF_DEPTH = 4
_PARAM_RE = re.compile(r"\{[^}]+\}")


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=20000)


class ChatBody(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=60)


class ExecuteBody(BaseModel):
    calls: list[dict[str, Any]] = Field(min_length=1, max_length=engine.MAX_PROPOSED_CALLS)


def _bearer(request: Request) -> str:
    return request.headers.get("authorization", "")


def _make_api_caller(request: Request) -> engine.ApiCaller:
    authorization = _bearer(request)
    app = request.app

    async def call(
        tool: Tool, path: str, query: dict[str, Any], body: dict[str, Any] | None
    ) -> engine.ApiResult:
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),  # type: ignore[arg-type]
                base_url=_BASE_URL,
                timeout=60.0,
            ) as client:
                response = await client.request(
                    tool.method,
                    path,
                    params=query or None,
                    json=body,
                    headers={"Authorization": authorization, "Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            return engine.ApiResult(False, 0, {"error": type(exc).__name__})
        try:
            data: Any = response.json()
        except ValueError:
            data = {"body": response.text[:500]} if response.text else None
        return engine.ApiResult(response.status_code < 400, response.status_code, data)

    return call


def _resolve(schema: Any, components: dict[str, Any], depth: int = 0) -> Any:
    """Inline ``$ref``s (bounded) so the model sees one self-contained schema."""
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str):
            if depth >= _REF_DEPTH:
                return {"type": "object"}
            target = components.get(ref.rsplit("/", 1)[-1], {})
            return _resolve(target, components, depth + 1)
        # Drop the "title" annotation only; a property *named* title must survive.
        return {
            k: _resolve(v, components, depth)
            for k, v in schema.items()
            if not (k == "title" and isinstance(v, str))
        }
    if isinstance(schema, list):
        return [_resolve(v, components, depth) for v in schema]
    return schema


def _make_describer(request: Request) -> engine.Describer:
    def describe(tool: Tool) -> dict[str, Any] | None:
        spec = request.app.openapi()
        want = _PARAM_RE.sub("{}", tool.path)
        for path, operations in (spec.get("paths") or {}).items():
            if _PARAM_RE.sub("{}", path) != want:
                continue
            operation = operations.get(tool.method.lower())
            content = ((operation or {}).get("requestBody") or {}).get("content") or {}
            schema = (content.get("application/json") or {}).get("schema")
            if schema:
                components = (spec.get("components") or {}).get("schemas") or {}
                return _resolve(schema, components)
        return None

    return describe


def _make_llm(config: ai_provider.AIConfig) -> engine.Llm:
    async def llm(system: str, user: str) -> str:
        result = await run_in_threadpool(
            ai_provider.generate_json_text,
            config,
            system=system,
            user=user,
            temperature=0.0,
        )
        return result.text

    return llm


def _unavailable(message: str, status: int = 409) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": "ASSISTANT_UNAVAILABLE", "message": message}}
    )


@router.post("/chat")
async def chat(
    body: ChatBody,
    request: Request,
    _tenant: TenantContext = Depends(require_tenant),
):
    config, problem = ai_provider.try_load_ai_config()
    if config is None:
        return _unavailable(
            problem or "The AI connection is not configured. Set RAI_AI_* on the server."
        )
    try:
        outcome = await engine.run_chat(
            [m.model_dump() for m in body.messages],
            llm=_make_llm(config),
            call_api=_make_api_caller(request),
            describe=_make_describer(request),
        )
    except ai_provider.AIProviderError as exc:
        return _unavailable(f"The AI provider could not answer ({exc.kind}). Try again.", 502)
    return {
        "reply": outcome.reply,
        "pending_action": outcome.pending_action,
        "ui_action": outcome.ui_action,
        "trace": outcome.trace,
    }


@router.post("/execute")
async def execute(
    body: ExecuteBody,
    request: Request,
    _tenant: TenantContext = Depends(require_tenant),
):
    try:
        results = await engine.execute_calls(body.calls, call_api=_make_api_caller(request))
    except engine.ToolArgError as exc:
        return JSONResponse(
            status_code=422, content={"error": {"code": "INVALID_ACTION", "message": str(exc)}}
        )
    return {"results": results}


def register(app) -> None:
    app.include_router(router)
