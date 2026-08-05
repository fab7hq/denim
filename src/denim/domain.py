"""Closed primitives shared by Denim's ask and seal operations."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ASK_ID_RE = re.compile(r"^ask_[0-9a-f]{32}$")
BATCH_ID_RE = re.compile(r"^batch_[0-9a-f]{32}$")
CAPABILITY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,119}$")
MAX_PROMPT_BYTES = 64 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024


@dataclass
class DenimError(Exception):
    """One bounded, machine-readable Denim failure."""

    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.context}


def canonical_json(value: Any) -> bytes:
    """Return the single canonical byte representation used for every digest."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise DenimError("DENIM_INVALID_JSON", "Value is not canonical JSON") from error
    if len(encoded) > MAX_JSON_BYTES:
        raise DenimError("DENIM_BOUNDS", "Canonical JSON exceeds the size limit")
    return encoded


def artifact_json(value: Any) -> bytes:
    """Return canonical JSON with the one required trailing newline."""

    return canonical_json(value) + b"\n"


def parse_json(content: bytes, *, name: str) -> Any:
    if len(content) > MAX_JSON_BYTES:
        raise DenimError("DENIM_BOUNDS", f"{name} exceeds the size limit")
    try:
        return json.loads(content, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, DenimError) as error:
        raise DenimError("DENIM_INVALID_JSON", f"{name} is not valid JSON") from error


def digest_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def digest_json(value: Any) -> str:
    return digest_bytes(canonical_json(value))


def bounded_text(value: Any, name: str, limit: int = MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise DenimError("DENIM_INVALID_INPUT", f"{name} must be bounded text")
    return value


def bounded_token(value: Any, name: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise DenimError(
            "DENIM_INVALID_INPUT", f"{name} must be a bounded lowercase token"
        )
    return value


def bounded_string_list(value: Any, name: str, *, limit: int = 64) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise DenimError("DENIM_INVALID_INPUT", f"{name} must be a bounded string list")
    result = [bounded_text(item, name, 4096) for item in value]
    if len(result) != len(set(result)):
        raise DenimError("DENIM_INVALID_INPUT", f"{name} must not contain duplicates")
    return result


def new_ask_id() -> str:
    return "ask_" + uuid.uuid4().hex


def new_batch_id() -> str:
    return "batch_" + uuid.uuid4().hex


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DenimError(
                "DENIM_INVALID_JSON", "JSON object contains duplicate fields"
            )
        value[key] = item
    return value
