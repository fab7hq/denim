"""Two-phase host-native ask preparation and exact-result capture."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import load_config
from .discovery import Capability, discover, resolve
from .domain import (
    ASK_ID_RE,
    DenimError,
    bounded_string_list,
    bounded_text,
    bounded_token,
    digest_bytes,
    new_ask_id,
)
from .store import ASK_SCHEMA, Store

TICKET_SCHEMA = "denim-ask-ticket-1"
PREPARE_FIELDS = {
    "phase",
    "host",
    "prompt",
    "candidates",
    "authority",
    "prohibitions",
    "target",
    "route",
    "use",
}
RECORD_FIELDS = {"phase", "ticket", "status", "result", "limitations", "effects"}
TICKET_FIELDS = {
    "schema",
    "ask_id",
    "prompt",
    "host",
    "capability",
    "enriched_prompt",
    "target",
    "authority",
    "prohibitions",
}


def handle_ask(
    workspace: Path, request: Any, *, home: Path | None = None
) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise DenimError("DENIM_INVALID_INPUT", "Ask request must be a JSON object")
    phase = request.get("phase")
    if phase == "prepare":
        return prepare(workspace, request, home=home)
    if phase == "record":
        return record(workspace, request)
    raise DenimError("DENIM_INVALID_INPUT", "Ask phase must be prepare or record")


def prepare(
    workspace: Path, request: dict[str, Any], *, home: Path | None = None
) -> dict[str, Any]:
    if set(request) != PREPARE_FIELDS:
        raise DenimError("DENIM_INVALID_INPUT", "Prepare request fields are invalid")
    root = workspace.resolve(strict=True)
    prompt = bounded_text(request.get("prompt"), "Exact ask", 64 * 1024)
    if not prompt:
        raise DenimError("DENIM_INVALID_INPUT", "Exact ask must not be empty")
    host = request.get("host")
    authority = bounded_string_list(request.get("authority"), "Granted authority")
    prohibitions = bounded_string_list(request.get("prohibitions"), "Ask prohibitions")
    target = bounded_text(request.get("target"), "Ask target", 4096)
    route = request.get("route")
    exact_id = request.get("use")
    if route is not None:
        route = bounded_token(route, "Ask route")
    if exact_id is not None and not isinstance(exact_id, str):
        raise DenimError("DENIM_INVALID_INPUT", "Exact capability ID is invalid")
    capabilities = discover(host, request.get("candidates"))
    config = load_config(root, home=home)
    ask_id = new_ask_id()
    try:
        capability = resolve(
            capabilities,
            config,
            query=prompt,
            authority=authority,
            route=route,
            exact_id=exact_id,
        )
    except DenimError as error:
        if error.code != "DENIM_CAPABILITY_UNAVAILABLE":
            raise
        enriched = _render_prompt(prompt, target, authority, prohibitions, None)
        result = str(error)
        stored = Store(root).write_ask(
            _record(
                ask_id=ask_id,
                prompt=prompt,
                host=host,
                capability=None,
                enriched=enriched,
                status="capability_unavailable",
                result=result,
                limitations=[error.message],
                effects=[],
            ),
            result,
        )
        return _outcome(stored.record)

    enriched = _render_prompt(prompt, target, authority, prohibitions, capability)
    ticket = {
        "schema": TICKET_SCHEMA,
        "ask_id": ask_id,
        "prompt": prompt,
        "host": host,
        "capability": capability.to_dict(),
        "enriched_prompt": enriched,
        "target": target,
        "authority": authority,
        "prohibitions": prohibitions,
    }
    return {
        "ok": True,
        "status": "ready",
        "ask_id": ask_id,
        "capability": capability.to_dict(),
        "enriched_prompt": enriched,
        "ticket": ticket,
    }


def record(workspace: Path, request: dict[str, Any]) -> dict[str, Any]:
    if set(request) != RECORD_FIELDS:
        raise DenimError("DENIM_INVALID_INPUT", "Record request fields are invalid")
    ticket = _ticket(request.get("ticket"))
    status = request.get("status")
    if status not in {"complete", "blocked"}:
        raise DenimError(
            "DENIM_INVALID_INPUT", "Recorded ask status must be complete or blocked"
        )
    result = bounded_text(request.get("result"), "Exact result", 1024 * 1024)
    limitations = bounded_string_list(request.get("limitations"), "Ask limitations")
    effects = bounded_string_list(request.get("effects"), "Ask effects")
    capability = discover(ticket["host"], [ticket["capability"]])[0]
    expected = _render_prompt(
        ticket["prompt"],
        ticket["target"],
        ticket["authority"],
        ticket["prohibitions"],
        capability,
    )
    if ticket["enriched_prompt"] != expected:
        raise DenimError("DENIM_INVALID_INPUT", "Ask ticket prompt binding is invalid")
    stored = Store(workspace).write_ask(
        _record(
            ask_id=ticket["ask_id"],
            prompt=ticket["prompt"],
            host=ticket["host"],
            capability=capability,
            enriched=expected,
            status=status,
            result=result,
            limitations=limitations,
            effects=effects,
        ),
        result,
    )
    return _outcome(stored.record)


def _ticket(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != TICKET_FIELDS
        or value.get("schema") != TICKET_SCHEMA
    ):
        raise DenimError("DENIM_INVALID_INPUT", "Ask ticket fields are invalid")
    ask_id = value.get("ask_id")
    if not isinstance(ask_id, str) or ASK_ID_RE.fullmatch(ask_id) is None:
        raise DenimError("DENIM_INVALID_INPUT", "Ask ticket identity is invalid")
    prompt = bounded_text(value.get("prompt"), "Exact ask", 64 * 1024)
    enriched = bounded_text(value.get("enriched_prompt"), "Enriched prompt", 64 * 1024)
    target = bounded_text(value.get("target"), "Ask target", 4096)
    authority = bounded_string_list(value.get("authority"), "Granted authority")
    prohibitions = bounded_string_list(value.get("prohibitions"), "Ask prohibitions")
    return {
        **value,
        "prompt": prompt,
        "enriched_prompt": enriched,
        "target": target,
        "authority": authority,
        "prohibitions": prohibitions,
    }


def _render_prompt(
    prompt: str,
    target: str,
    authority: list[str],
    prohibitions: list[str],
    capability: Capability | None,
) -> str:
    authority_text = (
        ", ".join(authority) if authority else "none beyond current host defaults"
    )
    result_contract = (
        f"exact capturable {capability.kind} result from {capability.id}"
        if capability is not None
        else "no delegated result because no eligible capability is available"
    )
    stop_conditions = (
        "; ".join(prohibitions)
        if prohibitions
        else "capability failure or authority denial"
    )
    rendered = (
        f"Exact ask: {prompt}\n"
        f"Target: {target}\n"
        f"Authority: {authority_text}\n"
        f"Result contract: {result_contract}\n"
        f"Stop conditions: {stop_conditions}"
    )
    return bounded_text(rendered, "Enriched prompt", 64 * 1024)


def _record(
    *,
    ask_id: str,
    prompt: str,
    host: str,
    capability: Capability | None,
    enriched: str,
    status: str,
    result: str,
    limitations: list[str],
    effects: list[str],
) -> dict[str, Any]:
    capability_value = capability.to_dict() if capability is not None else None
    return {
        "schema": ASK_SCHEMA,
        "ask_id": ask_id,
        "prompt": prompt,
        "prompt_digest": digest_bytes(prompt.encode("utf-8")),
        "host": host,
        "capability_id": capability.id if capability is not None else "unavailable",
        "capability_metadata_digest": (
            capability.metadata_digest
            if capability is not None
            else digest_bytes(b"null")
        ),
        "capability": capability_value,
        "enriched_prompt": enriched,
        "enriched_prompt_digest": digest_bytes(enriched.encode("utf-8")),
        "status": status,
        "result_digest": digest_bytes(result.encode("utf-8")),
        "limitations": limitations,
        "effects": effects,
    }


def _outcome(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "status": record["status"],
        "ask_id": record["ask_id"],
        "capability_id": record["capability_id"],
        "result_digest": record["result_digest"],
        "seal": "pending",
    }
