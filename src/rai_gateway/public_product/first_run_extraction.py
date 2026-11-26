"""Draft policy rules from uploaded files or text during first-run setup.

Nothing is saved. The AI systems do not exist yet, so the caller describes them (and
the tools they will use) with its own references, and rules come back bound to those.
Reading files reuses the sandboxed onboarding parsers; finding requirements reuses the
governance prompt, which keeps only items that quote the source verbatim.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from rai_gateway.core import ai_provider
from rai_gateway.onboarding import governance_ai
from rai_gateway.onboarding.discovery_ai import InvalidModelOutput
from rai_gateway.onboarding.extraction import configured_limits
from rai_gateway.onboarding.parsers.contracts import effective_options, segment_lines
from rai_gateway.onboarding.parsers.runner import run_parser
from rai_gateway.onboarding.storage import (
    StagedObject,
    UnsupportedMedia,
    detect_media_family,
)
from rai_gateway.public_product.first_run import FirstRunError

MAX_FILES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 50_000
# All documents together. Longer input is refused, never silently cut.
MAX_TOTAL_CHARS = 120_000
_PARTITION_CHARS = 8_000
_PARALLEL_CALLS = 4
_MIN_TIMEOUT_SECONDS = 90.0
_PARSER_SECONDS = 60.0
_MAX_TOOLS_IN_PROMPT = 300
_APPROVER_ROLES = ("business_approver", "rai_officer", "admin")
_DEFAULT_WINDOW = 86_400
AI_NOT_CONFIGURED = (
    "Add your LLM credential (RAI_AI_*) to .env and restart to extract rules, "
    "or add rules manually."
)


class ContextSystem(BaseModel):
    ref: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)


class ContextTool(BaseModel):
    ref: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=200)
    system_refs: list[str] = Field(default_factory=list, max_length=20)


class ExtractionContext(BaseModel):
    """The setup draft the rules will apply to."""

    systems: list[ContextSystem] = Field(min_length=1, max_length=20)
    tools: list[ContextTool] = Field(default_factory=list, max_length=500)


@dataclasses.dataclass
class Upload:
    filename: str
    content_type: str | None
    data: bytes


# -------------------------------------------------------------------- documents


def _read_file(upload: Upload) -> tuple[list[dict[str, Any]], str | None]:
    """``(blocks, problem)``. A problem with no blocks means nothing was read."""
    if len(upload.data) > MAX_FILE_BYTES:
        return [], "The file is larger than 10 MB."
    with tempfile.TemporaryDirectory(prefix="rai-first-run-") as folder:
        path = Path(folder) / "upload"
        path.write_bytes(upload.data)
        staged = StagedObject(
            path=path,
            sha256=hashlib.sha256(upload.data).hexdigest(),
            byte_length=len(upload.data),
            head=upload.data[:65536],
        )
        try:
            family = detect_media_family(upload.filename, upload.content_type, staged)
        except UnsupportedMedia as exc:
            return [], f"{exc}. Use PDF, DOCX, XLSX, CSV, TXT or Markdown."
        limits = configured_limits()
        limits = dataclasses.replace(
            limits, wall_seconds=min(limits.wall_seconds, _PARSER_SECONDS)
        )
        parsed = run_parser(
            str(path),
            family,
            "FILE",
            effective_options(family, "FILE", {}),
            limits,
        )
    blocks = [b for b in parsed.get("blocks") or [] if (b.get("text") or "").strip()]
    outcome = parsed.get("outcome")
    if outcome == "COMPLETE" and blocks:
        return blocks, None
    if outcome == "NEEDS_OCR":
        problem = "It looks like a scanned image. Upload a text or searchable copy."
    elif blocks:
        problem = "Only part of this file could be read."
    else:
        problem = "No text could be read from this file."
    return blocks, problem


def _text_blocks(text: str) -> list[dict[str, Any]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [
        {"kind": "TEXT", "text": chunk, "locator": {"lines": [start, end]}}
        for start, end, chunk in segment_lines(lines)
        if chunk.strip()
    ]


def _documents(
    uploads: list[Upload], text: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read every input into labelled blocks."""
    inputs: list[tuple[str, list[dict[str, Any]], str | None]] = []
    if text.strip():
        inputs.append(("Written policy", _text_blocks(text), None))
    for upload in uploads:
        blocks, problem = _read_file(upload)
        name = Path(upload.filename.replace("\\", "/")).name[:200] or "Uploaded file"
        inputs.append((name, blocks, problem))

    documents: list[dict[str, Any]] = []
    all_blocks: list[dict[str, Any]] = []
    for index, (title, blocks, problem) in enumerate(inputs, start=1):
        ref = f"doc{index}"
        for block in blocks:
            label = f"B{len(all_blocks) + 1}"
            all_blocks.append(
                {**block, "label": label, "block_id": label, "document": ref}
            )
        documents.append(
            {
                "ref": ref,
                "title": title,
                "problem": problem,
                "read": bool(blocks),
                "text": "\n\n".join(b["text"] for b in blocks),
            }
        )
    return documents, all_blocks


def _partitions(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    parts: list[list[dict[str, Any]]] = []
    size = 0
    for block in blocks:
        length = len(block["text"])
        if not parts or size + length > _PARTITION_CHARS:
            parts.append([])
            size = 0
        parts[-1].append(block)
        size += length
    return parts


# --------------------------------------------------------------------------- AI


def _analyze(
    config: ai_provider.AIConfig,
    blocks: list[dict[str, Any]],
    systems: list[tuple[str, str]],
    tool_names: list[str],
) -> tuple[list[list[dict[str, Any]]], int, int]:
    """``(requirements per call, dropped items, unreadable replies)``."""
    prompt = governance_ai.build_user_prompt(blocks, systems)
    if tool_names:
        listing = "\n".join(f"- {name}" for name in tool_names)
        prompt = (
            "Available tools (when a requirement is about one of them, use its exact "
            f"name as tool_selector):\n{listing}\n\n{prompt}"
        )
    for attempt in (1, 2):
        try:
            result = ai_provider.generate_json_text(
                config,
                system=governance_ai.system_prompt(),
                user=prompt,
                schema=governance_ai.OUTPUT_SCHEMA,
                temperature=0.0,
                timeout_seconds=max(config.timeout_seconds, _MIN_TIMEOUT_SECONDS),
            )
            parsed = governance_ai.parse_partition(
                result.text, blocks, {key for key, _ in systems}
            )
        except ai_provider.AIProviderError as exc:
            # A reply that did not fit: ask about half as much at a time.
            if exc.kind != "truncated" or len(blocks) < 2:
                raise
            middle = len(blocks) // 2
            first = _analyze(config, blocks[:middle], systems, tool_names)
            second = _analyze(config, blocks[middle:], systems, tool_names)
            return (
                first[0] + second[0],
                first[1] + second[1],
                first[2] + second[2],
            )
        except InvalidModelOutput:
            if attempt == 2:
                return [], 0, 1
            continue
        dropped = parsed.dropped_invalid + parsed.dropped_unverified
        return [parsed.requirements], dropped, 0
    raise AssertionError("unreachable")  # pragma: no cover


# ------------------------------------------------------------------------ rules


def _words(value: str) -> str:
    return re.sub(r"[\W_]+", " ", value.casefold()).strip()


def match_tool(selector: str | None, tools: list[ContextTool]) -> ContextTool | None:
    """The one tool the policy names, or None when that is not certain."""
    wanted = _words(selector or "")
    if not wanted:
        return None
    exact = [t for t in tools if _words(t.name) == wanted]
    if exact:
        return exact[0] if len(exact) == 1 else None
    words = set(wanted.split())
    close = [
        t
        for t in tools
        if words <= set(_words(t.name).split()) or set(_words(t.name).split()) <= words
    ]
    return close[0] if len(close) == 1 else None


def _approver(stated: str | None, warnings: list[str]) -> str:
    wanted = _words(stated or "").replace(" ", "_")
    if wanted in _APPROVER_ROLES:
        return wanted
    if stated:
        warnings.append(
            f"The policy names '{stated}' as approver. Choose the matching role."
        )
    return "business_approver"


def _draft_rule(
    family: str,
    generated: dict[str, Any],
    system_refs: list[str],
    context: ExtractionContext,
) -> dict[str, Any]:
    warnings: list[str] = []
    every_system = [s.ref for s in context.systems]
    if not system_refs and len(every_system) > 1:
        warnings.append("The policy names no AI system, so all of them are selected.")
    targets = system_refs or every_system
    rule: dict[str, Any] = {
        "title": str(generated.get("title") or "Policy rule")[:200],
        "system_refs": targets,
        "enabled": True,
        "warnings": warnings,
    }
    if family == "content_rule":
        effect = generated.get("effect") or "DENY"
        rule.update(
            family="content",
            content={
                "content": generated.get("content") or rule["title"],
                "effect": effect,
                "applies_to": generated.get("applies_to") or "BOTH",
                "approver_role": _approver(None, warnings)
                if effect == "REQUIRE_APPROVAL"
                else None,
            },
        )
    elif family == "tool_rule":
        tool = match_tool(generated.get("tool_selector"), context.tools)
        if tool is None:
            named = generated.get("tool_selector")
            warnings.append(
                f"Choose the tool this rule is about{f' ({named})' if named else ''}."
            )
        else:
            usable = [ref for ref in targets if ref in tool.system_refs]
            if not usable:
                warnings.append(f"No selected AI system uses {tool.name}.")
            elif system_refs and len(usable) < len(targets):
                warnings.append(f"Only systems that use {tool.name} are selected.")
            targets = usable
        effect = generated.get("effect") or "REQUIRE_APPROVAL"
        # The source's conditions cannot be mapped to tool parameters, so the rule
        # would apply to every call: the person switches it on deliberately.
        warnings.append(
            "Disabled: it would apply to every call of the tool. Add a condition or "
            "switch it on."
        )
        rule.update(
            family="tool",
            enabled=False,
            system_refs=targets,
            tool={
                "tool_ref": tool.ref if tool else None,
                "effect": effect,
                "approver_role": _approver(generated.get("approval_role"), warnings)
                if effect == "REQUIRE_APPROVAL"
                else None,
                "conditions": [],
            },
        )
    else:
        window = generated.get("window_seconds")
        if not window:
            warnings.append("The policy gives no fixed window. One day is selected.")
            window = _DEFAULT_WINDOW
        elif generated.get("window_intent") == "month":
            warnings.append("A calendar month is applied as a rolling 30 days.")
        currency = str(generated.get("currency") or "").upper()
        if generated.get("metric") == "COST" and currency not in ("", "USD"):
            warnings.append(
                f"Disabled: the limit is in {currency}, but cost is measured in USD. "
                "Convert the limit, then switch it on."
            )
            rule["enabled"] = False
        rule.update(
            family="budget",
            budget={
                "metric": generated["metric"],
                "limit_value": generated["limit_value"],
                "window_seconds": window,
                "effect": "DENY",
            },
        )
    return rule


def extract_rules(
    context: ExtractionContext, uploads: list[Upload], text: str
) -> dict[str, Any]:
    config, problem = ai_provider.try_load_ai_config()
    if config is None:
        raise FirstRunError(
            problem or AI_NOT_CONFIGURED, http_status=409, code="AI_NOT_CONFIGURED"
        )
    if len(uploads) > MAX_FILES:
        raise FirstRunError(f"Upload at most {MAX_FILES} files at a time.")
    if len(text) > MAX_TEXT_CHARS:
        raise FirstRunError(f"The text is longer than {MAX_TEXT_CHARS:,} characters.")
    if not text.strip() and not uploads:
        raise FirstRunError("Upload a policy file or write the policy first.")

    documents, blocks = _documents(uploads, text)
    total = sum(len(b["text"]) for b in blocks)
    if total > MAX_TOTAL_CHARS:
        raise FirstRunError(
            f"These documents hold {total:,} characters; the limit is "
            f"{MAX_TOTAL_CHARS:,}. Upload fewer or shorter files.",
            code="TOO_LARGE",
        )
    if not blocks:
        reasons = "; ".join(f"{d['title']}: {d['problem']}" for d in documents)
        raise FirstRunError(
            reasons or "There is no text to analyze.", code="UNREADABLE"
        )

    # Short keys for the model; the caller's references never leave the server.
    refs = {f"S{i}": s.ref for i, s in enumerate(context.systems, start=1)}
    systems = [(f"S{i}", s.name) for i, s in enumerate(context.systems, start=1)]
    tool_names = list(dict.fromkeys(t.name for t in context.tools))
    parts = _partitions(blocks)
    try:
        with ThreadPoolExecutor(max_workers=_PARALLEL_CALLS) as pool:
            replies = list(
                pool.map(
                    lambda part: _analyze(
                        config, part, systems, tool_names[:_MAX_TOOLS_IN_PROMPT]
                    ),
                    parts,
                )
            )
    except ai_provider.AIProviderError as exc:
        raise FirstRunError(
            f"The AI provider could not analyze the policy ({exc.kind}). Try again, "
            "or add rules manually.",
            http_status=502,
            code="AI_FAILED",
        ) from exc

    requirements = [reqs for reply in replies for reqs in reply[0]]
    dropped = sum(reply[1] for reply in replies)
    failed = sum(reply[2] for reply in replies)
    if failed and not requirements:
        raise FirstRunError(
            "The AI reply could not be read. Try again, or add rules manually.",
            http_status=502,
            code="AI_FAILED",
        )
    clauses, proposals = governance_ai.compile_requirements(requirements)
    by_clause: dict[int, dict[str, Any]] = {}
    for proposal in proposals:
        # One proposal per bound system; the rule is the same, so keep the first.
        by_clause.setdefault(proposal["source_clause_ordinal"], proposal)
    block_document = {b["block_id"]: b["document"] for b in blocks}
    titles = {d["ref"]: d["title"] for d in documents}

    rules: list[dict[str, Any]] = []
    notes: list[dict[str, str]] = []
    for clause in clauses:
        proposal = by_clause[clause["ordinal"]]
        family = proposal["proposal_family"]
        document = block_document[clause["evidence"][0]["block_id"]]
        generated = proposal["generated"]
        if family not in ("content_rule", "tool_rule", "runtime_budget"):
            notes.append(
                {
                    "title": generated["title"],
                    "quote": clause["text"],
                    "document": titles[document],
                }
            )
            continue
        rule = _draft_rule(
            family, generated, [refs[k] for k in clause["systems"]], context
        )
        rules.append({**rule, "document_ref": document, "quote": clause["text"]})

    warnings = [
        f"{d['title']}: {d['problem']}" for d in documents if d["problem"] is not None
    ]
    if failed:
        warnings.append(
            f"{failed} part(s) of the documents could not be analyzed. Check that "
            "nothing is missing."
        )
    if dropped:
        warnings.append(
            f"{dropped} suggestion(s) were ignored because they did not quote the "
            "policy."
        )
    return {
        "documents": [
            {"ref": d["ref"], "title": d["title"], "text": d["text"]}
            for d in documents
            if d["read"]
        ],
        "rules": rules,
        "notes": notes,
        "warnings": warnings,
    }
