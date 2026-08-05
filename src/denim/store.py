"""Immutable ask and batch artifacts below the Fab7 runtime boundary."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .discovery import discover
from .domain import (
    ASK_ID_RE,
    BATCH_ID_RE,
    DIGEST_RE,
    MAX_PROMPT_BYTES,
    MAX_RESULT_BYTES,
    DenimError,
    artifact_json,
    bounded_string_list,
    bounded_text,
    digest_bytes,
    parse_json,
)

ASK_SCHEMA = "denim-ask-1"
BATCH_SCHEMA = "denim-seal-batch-1"
ASK_STATUSES = {"complete", "blocked", "capability_unavailable"}
ASK_FIELDS = {
    "schema",
    "ask_id",
    "prompt",
    "prompt_digest",
    "host",
    "capability_id",
    "capability_metadata_digest",
    "capability",
    "enriched_prompt",
    "enriched_prompt_digest",
    "status",
    "result_digest",
    "limitations",
    "effects",
}
BATCH_FIELDS = {"schema", "batch_id", "asks"}
BATCH_ASK_FIELDS = {"ask_id", "record_digest", "result_digest", "status"}
_ASK_FILE_RE = re.compile(r"^(ask_[0-9a-f]{32})\.json$")
_BATCH_FILE_RE = re.compile(r"^(batch_[0-9a-f]{32})\.json$")


@dataclass(frozen=True)
class AskArtifact:
    record: dict[str, Any]
    record_digest: str
    result: str


@dataclass(frozen=True)
class BatchArtifact:
    batch: dict[str, Any]
    digest: str


class Store:
    def __init__(self, workspace: Path):
        try:
            self.workspace = workspace.resolve(strict=True)
        except OSError as error:
            raise DenimError(
                "DENIM_WORKSPACE_INVALID", "Workspace does not exist"
            ) from error
        if not self.workspace.is_dir():
            raise DenimError("DENIM_WORKSPACE_INVALID", "Workspace must be a directory")
        fab7 = self.workspace / ".fab7"
        project = fab7 / "project.json"
        if fab7.is_symlink() or project.is_symlink() or not project.is_file():
            raise DenimError(
                "DENIM_NOT_INITIALIZED", "Run fab7 init in the workspace first"
            )
        self.root = fab7 / "denim"
        self.asks = self.root / "asks"
        self.batches = self.root / "batches"
        self.temporary = self.root / ".tmp"
        self.lock = self.root / "seal.lock"

    def initialize(self) -> None:
        for path in (self.root, self.asks, self.batches, self.temporary):
            if path.is_symlink():
                raise DenimError(
                    "DENIM_STATE_INVALID",
                    "Denim state directories must not be symlinked",
                )
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)
        ignore = self.root / ".gitignore"
        if ignore.is_symlink():
            raise DenimError(
                "DENIM_STATE_INVALID", "Denim nested ignore boundary is invalid"
            )
        if not ignore.exists():
            self._commit(ignore, b"*\n")
        elif not ignore.is_file() or _read(ignore, 2, "Denim nested ignore") != b"*\n":
            raise DenimError(
                "DENIM_STATE_INVALID", "Denim nested ignore boundary is invalid"
            )

    def write_ask(self, record: dict[str, Any], result: str) -> AskArtifact:
        self.initialize()
        validated = validate_ask(record, result)
        ask_id = validated["ask_id"]
        result_bytes = result.encode("utf-8")
        self._commit(self.asks / f"{ask_id}.result", result_bytes)
        record_bytes = artifact_json(validated)
        self._commit(self.asks / f"{ask_id}.json", record_bytes)
        return AskArtifact(validated, digest_bytes(record_bytes), result)

    def read_asks(self) -> list[AskArtifact]:
        if not self.asks.exists():
            return []
        if self.asks.is_symlink() or not self.asks.is_dir():
            raise DenimError("DENIM_STATE_INVALID", "Denim asks path is invalid")
        result: list[AskArtifact] = []
        for path in sorted(self.asks.iterdir(), key=lambda item: item.name):
            match = _ASK_FILE_RE.fullmatch(path.name)
            if match is None:
                continue
            if path.is_symlink() or not path.is_file():
                raise DenimError(
                    "DENIM_STATE_INVALID", "Ask records must be regular files"
                )
            _private_file(path, "Ask record")
            record_bytes = _read(path, 2 * 1024 * 1024, "Ask record")
            if (
                artifact_json(parse_json(record_bytes, name="Ask record"))
                != record_bytes
            ):
                raise DenimError("DENIM_STATE_INVALID", "Ask record is not canonical")
            record = parse_json(record_bytes, name="Ask record")
            result_path = self.asks / f"{match.group(1)}.result"
            if result_path.is_symlink() or not result_path.is_file():
                raise DenimError(
                    "DENIM_STATE_INVALID", "Ask result is missing or invalid"
                )
            _private_file(result_path, "Ask result")
            try:
                result_text = _read(result_path, MAX_RESULT_BYTES, "Ask result").decode(
                    "utf-8"
                )
            except UnicodeDecodeError as error:
                raise DenimError(
                    "DENIM_STATE_INVALID", "Ask result is not UTF-8 text"
                ) from error
            validated = validate_ask(record, result_text)
            if validated["ask_id"] != match.group(1):
                raise DenimError(
                    "DENIM_STATE_INVALID", "Ask record path does not match its identity"
                )
            result.append(
                AskArtifact(validated, digest_bytes(record_bytes), result_text)
            )
        return result

    def write_batch(self, batch: dict[str, Any]) -> BatchArtifact:
        self.initialize()
        validated = validate_batch(batch)
        content = artifact_json(validated)
        self._commit(self.batches / f"{validated['batch_id']}.json", content)
        return BatchArtifact(validated, digest_bytes(content))

    def read_batches(self) -> list[BatchArtifact]:
        if not self.batches.exists():
            return []
        if self.batches.is_symlink() or not self.batches.is_dir():
            raise DenimError("DENIM_STATE_INVALID", "Denim batches path is invalid")
        result: list[BatchArtifact] = []
        for path in sorted(self.batches.iterdir(), key=lambda item: item.name):
            match = _BATCH_FILE_RE.fullmatch(path.name)
            if match is None:
                continue
            if path.is_symlink() or not path.is_file():
                raise DenimError(
                    "DENIM_STATE_INVALID", "Batch receipts must be regular files"
                )
            _private_file(path, "Batch receipt")
            content = _read(path, 2 * 1024 * 1024, "Batch receipt")
            batch = parse_json(content, name="Batch receipt")
            if artifact_json(batch) != content:
                raise DenimError(
                    "DENIM_STATE_INVALID", "Batch receipt is not canonical"
                )
            validated = validate_batch(batch)
            if validated["batch_id"] != match.group(1):
                raise DenimError(
                    "DENIM_STATE_INVALID", "Batch path does not match its identity"
                )
            result.append(BatchArtifact(validated, digest_bytes(content)))
        return result

    def _commit(self, destination: Path, content: bytes) -> None:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="commit-", dir=self.temporary
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            try:
                os.link(temporary_path, destination)
            except FileExistsError as error:
                raise DenimError(
                    "DENIM_IMMUTABLE_CONFLICT",
                    "Immutable Denim artifact already exists",
                    {"path": str(destination)},
                ) from error
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary_path.unlink(missing_ok=True)


def validate_ask(record: Any, result: str) -> dict[str, Any]:
    if (
        not isinstance(record, dict)
        or set(record) != ASK_FIELDS
        or record.get("schema") != ASK_SCHEMA
    ):
        raise DenimError("DENIM_STATE_INVALID", "Ask record fields are invalid")
    ask_id = record.get("ask_id")
    if not isinstance(ask_id, str) or ASK_ID_RE.fullmatch(ask_id) is None:
        raise DenimError("DENIM_STATE_INVALID", "Ask identity is invalid")
    prompt = bounded_text(record.get("prompt"), "Ask prompt", MAX_PROMPT_BYTES)
    enriched = bounded_text(
        record.get("enriched_prompt"), "Enriched prompt", MAX_PROMPT_BYTES
    )
    result = bounded_text(result, "Ask result", MAX_RESULT_BYTES)
    status = record.get("status")
    if status not in ASK_STATUSES:
        raise DenimError("DENIM_STATE_INVALID", "Ask status is invalid")
    if record.get("host") not in {"claude", "codex"}:
        raise DenimError("DENIM_STATE_INVALID", "Ask host is invalid")
    capability = record.get("capability")
    if status == "capability_unavailable":
        if capability is not None or record.get("capability_id") != "unavailable":
            raise DenimError(
                "DENIM_STATE_INVALID", "Unavailable ask capability is invalid"
            )
        metadata_digest = digest_bytes(b"null")
    else:
        if not isinstance(capability, dict):
            raise DenimError("DENIM_STATE_INVALID", "Ask capability is missing")
        normalized = discover(record.get("host"), [capability])[0]
        capability = normalized.to_dict()
        if record.get("capability_id") != normalized.id:
            raise DenimError(
                "DENIM_STATE_INVALID", "Ask capability identity does not match"
            )
        metadata_digest = normalized.metadata_digest
    expected = {
        "prompt_digest": digest_bytes(prompt.encode("utf-8")),
        "capability_metadata_digest": metadata_digest,
        "enriched_prompt_digest": digest_bytes(enriched.encode("utf-8")),
        "result_digest": digest_bytes(result.encode("utf-8")),
    }
    if any(
        record.get(key) != value or DIGEST_RE.fullmatch(value) is None
        for key, value in expected.items()
    ):
        raise DenimError("DENIM_STATE_INVALID", "Ask digest binding is invalid")
    limitations = bounded_string_list(record.get("limitations"), "Ask limitations")
    effects = bounded_string_list(record.get("effects"), "Ask effects")
    return {
        **record,
        "capability": capability,
        "limitations": limitations,
        "effects": effects,
    }


def validate_batch(batch: Any) -> dict[str, Any]:
    if (
        not isinstance(batch, dict)
        or set(batch) != BATCH_FIELDS
        or batch.get("schema") != BATCH_SCHEMA
    ):
        raise DenimError("DENIM_STATE_INVALID", "Batch receipt fields are invalid")
    batch_id = batch.get("batch_id")
    rows = batch.get("asks")
    if (
        not isinstance(batch_id, str)
        or BATCH_ID_RE.fullmatch(batch_id) is None
        or not isinstance(rows, list)
        or not rows
        or len(rows) > 10_000
    ):
        raise DenimError("DENIM_STATE_INVALID", "Batch identity or ask list is invalid")
    identifiers: list[str] = []
    normalized: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != BATCH_ASK_FIELDS:
            raise DenimError("DENIM_STATE_INVALID", "Batch ask fields are invalid")
        ask_id = row.get("ask_id")
        if not isinstance(ask_id, str) or ASK_ID_RE.fullmatch(ask_id) is None:
            raise DenimError("DENIM_STATE_INVALID", "Batch ask identity is invalid")
        if row.get("status") not in ASK_STATUSES:
            raise DenimError("DENIM_STATE_INVALID", "Batch ask status is invalid")
        for field in ("record_digest", "result_digest"):
            if (
                not isinstance(row.get(field), str)
                or DIGEST_RE.fullmatch(row[field]) is None
            ):
                raise DenimError("DENIM_STATE_INVALID", "Batch ask digest is invalid")
        identifiers.append(ask_id)
        normalized.append(dict(row))
    if identifiers != sorted(identifiers) or len(identifiers) != len(set(identifiers)):
        raise DenimError(
            "DENIM_STATE_INVALID", "Batch asks must be uniquely and canonically ordered"
        )
    return {"schema": BATCH_SCHEMA, "batch_id": batch_id, "asks": normalized}


def batch_from_asks(batch_id: str, asks: Iterable[AskArtifact]) -> dict[str, Any]:
    rows = [
        {
            "ask_id": item.record["ask_id"],
            "record_digest": item.record_digest,
            "result_digest": item.record["result_digest"],
            "status": item.record["status"],
        }
        for item in sorted(asks, key=lambda item: item.record["ask_id"])
    ]
    return validate_batch({"schema": BATCH_SCHEMA, "batch_id": batch_id, "asks": rows})


def _read(path: Path, limit: int, name: str) -> bytes:
    try:
        with path.open("rb") as handle:
            content = handle.read(limit + 1)
    except OSError as error:
        raise DenimError("DENIM_STATE_INVALID", f"{name} cannot be read") from error
    if len(content) > limit:
        raise DenimError("DENIM_BOUNDS", f"{name} exceeds the size limit")
    return content


def _private_file(path: Path, name: str) -> None:
    try:
        mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
    except OSError as error:
        raise DenimError(
            "DENIM_STATE_INVALID", f"{name} cannot be inspected"
        ) from error
    if mode & 0o077:
        raise DenimError("DENIM_STATE_INVALID", f"{name} permissions are too broad")
