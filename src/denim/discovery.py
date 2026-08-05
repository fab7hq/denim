"""Host-provided live capability normalization and deterministic resolution."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from .config import ORIGINS, ResolutionConfig
from .domain import (
    CAPABILITY_ID_RE,
    DIGEST_RE,
    DenimError,
    bounded_string_list,
    bounded_text,
    digest_json,
)

HOSTS = {"claude", "codex"}
KINDS = {"agent", "api", "command", "skill", "tool"}
DESCRIPTOR_FIELDS = {
    "id",
    "host",
    "origin",
    "kind",
    "description",
    "provides",
    "visible",
    "callable",
    "result_capture",
    "required_authority",
    "priority",
    "metadata_digest",
}
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Capability:
    id: str
    host: str
    origin: str
    kind: str
    description: str
    provides: tuple[str, ...]
    visible: bool
    callable: bool
    result_capture: bool
    required_authority: tuple[str, ...]
    priority: int
    metadata_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "host": self.host,
            "origin": self.origin,
            "kind": self.kind,
            "description": self.description,
            "provides": list(self.provides),
            "visible": self.visible,
            "callable": self.callable,
            "result_capture": self.result_capture,
            "required_authority": list(self.required_authority),
            "priority": self.priority,
            "metadata_digest": self.metadata_digest,
        }


def discover(host: str, values: Any) -> list[Capability]:
    """Normalize one live snapshot supplied by the selected host skill."""

    if host == "claude":
        return _discover_claude(values)
    if host == "codex":
        return _discover_codex(values)
    raise DenimError(
        "DENIM_HOST_UNSUPPORTED", "Installed package host must be claude or codex"
    )


def _discover_claude(values: Any) -> list[Capability]:
    return _normalize("claude", values)


def _discover_codex(values: Any) -> list[Capability]:
    return _normalize("codex", values)


def _normalize(host: str, values: Any) -> list[Capability]:
    if not isinstance(values, list) or len(values) > 256:
        raise DenimError(
            "DENIM_DISCOVERY_INVALID", "Capability discovery must be a bounded list"
        )
    result = [_capability(host, value) for value in values]
    identifiers = [item.id for item in result]
    if len(identifiers) != len(set(identifiers)):
        raise DenimError(
            "DENIM_DISCOVERY_INVALID", "Discovered capability IDs must be unique"
        )
    return result


def _capability(host: str, value: Any) -> Capability:
    if not isinstance(value, dict):
        raise DenimError(
            "DENIM_DISCOVERY_INVALID", "Capability descriptor fields are invalid"
        )
    fields = set(value)
    if fields != DESCRIPTOR_FIELDS and fields != DESCRIPTOR_FIELDS - {
        "metadata_digest"
    }:
        raise DenimError(
            "DENIM_DISCOVERY_INVALID", "Capability descriptor fields are invalid"
        )
    identifier = value.get("id")
    if (
        not isinstance(identifier, str)
        or CAPABILITY_ID_RE.fullmatch(identifier) is None
    ):
        raise DenimError("DENIM_DISCOVERY_INVALID", "Capability ID is invalid")
    if value.get("host") != host:
        raise DenimError(
            "DENIM_DISCOVERY_INVALID",
            "Capability host does not match the installed package",
        )
    origin = value.get("origin")
    kind = value.get("kind")
    if origin not in ORIGINS or kind not in KINDS:
        raise DenimError(
            "DENIM_DISCOVERY_INVALID", "Capability origin or kind is invalid"
        )
    description = bounded_text(value.get("description"), "Capability description", 4096)
    provides = tuple(
        sorted(bounded_string_list(value.get("provides"), "Capability provides"))
    )
    authority = tuple(
        sorted(
            bounded_string_list(value.get("required_authority"), "Capability authority")
        )
    )
    flags = tuple(value.get(name) for name in ("visible", "callable", "result_capture"))
    if any(type(flag) is not bool for flag in flags):
        raise DenimError("DENIM_DISCOVERY_INVALID", "Capability flags are invalid")
    priority = value.get("priority")
    if type(priority) is not int or not -10_000 <= priority <= 10_000:
        raise DenimError("DENIM_DISCOVERY_INVALID", "Capability priority is invalid")
    base = {
        "id": identifier,
        "host": host,
        "origin": origin,
        "kind": kind,
        "description": description,
        "provides": list(provides),
        "visible": flags[0],
        "callable": flags[1],
        "result_capture": flags[2],
        "required_authority": list(authority),
        "priority": priority,
    }
    metadata_digest = digest_json(base)
    supplied = value.get("metadata_digest")
    if supplied is not None and (
        not isinstance(supplied, str)
        or DIGEST_RE.fullmatch(supplied) is None
        or supplied != metadata_digest
    ):
        raise DenimError(
            "DENIM_DISCOVERY_INVALID", "Capability metadata digest does not match"
        )
    return Capability(
        identifier,
        host,
        origin,
        kind,
        description,
        provides,
        flags[0],
        flags[1],
        flags[2],
        authority,
        priority,
        metadata_digest,
    )


def resolve(
    capabilities: Iterable[Capability],
    config: ResolutionConfig,
    *,
    query: str,
    authority: Iterable[str],
    route: str | None = None,
    exact_id: str | None = None,
) -> Capability:
    """Select one eligible live capability without a host/scenario mapping."""

    candidates = [_annotate(item, config) for item in capabilities]
    granted = set(authority)
    eligible = [
        item
        for item in candidates
        if item.visible
        and item.callable
        and item.result_capture
        and set(item.required_authority).issubset(granted)
    ]

    if exact_id is not None:
        return _pinned(eligible, exact_id, "Exact capability")

    configured = config.routes.get(route) if route is not None else None
    if configured is not None:
        pinned = next((item for item in eligible if item.id == configured.use), None)
        if pinned is not None:
            return pinned
        if not configured.allow_fallback:
            raise DenimError(
                "DENIM_CAPABILITY_UNAVAILABLE",
                "Configured capability is not eligible in current discovery",
                {"capability_id": configured.use},
            )

    order = {origin: index for index, origin in enumerate(config.order)}
    specific: list[tuple[tuple[int, int, int, str], Capability]] = []
    wildcard: list[tuple[tuple[int, int, str], Capability]] = []
    for item in eligible:
        match = _specific_match(item, query, route)
        if match > 0:
            specific.append(
                ((order[item.origin], -match, -item.priority, item.id), item)
            )
        elif "*" in item.provides:
            wildcard.append(((order[item.origin], -item.priority, item.id), item))
    if specific:
        specific.sort(key=lambda row: row[0])
        return specific[0][1]
    if wildcard:
        wildcard.sort(key=lambda row: row[0])
        return wildcard[0][1]
    raise DenimError(
        "DENIM_CAPABILITY_UNAVAILABLE",
        "No eligible discovered capability matches this ask",
    )


def _pinned(
    capabilities: Iterable[Capability], identifier: str, label: str
) -> Capability:
    if CAPABILITY_ID_RE.fullmatch(identifier) is None:
        raise DenimError("DENIM_INVALID_INPUT", f"{label} ID is invalid")
    selected = next((item for item in capabilities if item.id == identifier), None)
    if selected is None:
        raise DenimError(
            "DENIM_CAPABILITY_UNAVAILABLE",
            f"{label} is not eligible in current discovery",
            {"capability_id": identifier},
        )
    return selected


def _annotate(item: Capability, config: ResolutionConfig) -> Capability:
    annotation = config.capabilities.get(item.id)
    if annotation is None:
        return item
    provides = annotation.provides if annotation.provides is not None else item.provides
    priority = annotation.priority if annotation.priority is not None else item.priority
    base = {**item.to_dict(), "provides": list(provides), "priority": priority}
    base.pop("metadata_digest")
    return replace(
        item, provides=provides, priority=priority, metadata_digest=digest_json(base)
    )


def _specific_match(item: Capability, query: str, route: str | None) -> int:
    target_words = set(_WORD_RE.findall((route or query).lower()))
    if not target_words:
        return 0
    specific = [value for value in item.provides if value != "*"]
    provided_words = set(_WORD_RE.findall(" ".join(specific).lower()))
    overlap = len(target_words & provided_words)
    if route is not None and route in specific:
        overlap += 100
    return overlap
