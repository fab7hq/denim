"""The single bounded public Fab7 subprocess and ledger validation boundary."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import DIGEST_RE, DenimError, bounded_text, parse_json
from .store import BatchArtifact, Store

ACTOR = "extension:fab7hq/denim"
SUBJECT_KIND = "denim-ask-batch"
FAB7_TIMEOUT_SECONDS = 60
PROCESS_TIMEOUT_SECONDS = 65
MAX_PROCESS_OUTPUT = 1024 * 1024
MAX_LEDGER_BYTES = 8 * 1024 * 1024
_RECORD_ID_RE = re.compile(r"^rec_[0-9a-f]{32}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_CLAIM_FIELDS = {
    "v",
    "id",
    "type",
    "work_item",
    "created_at",
    "actor",
    "summary",
    "subject",
}
_EVIDENCE_FIELDS = {
    "v",
    "id",
    "type",
    "work_item",
    "created_at",
    "actor",
    "claim",
    "subject_digest",
    "command_digest",
    "exit_code",
    "output_digest",
    "provenance",
}
_PAYLOAD_FIELDS = {"ok", "claim", "evidence", "path", "lines", "timed_out"}


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


Runner = Callable[[list[str], Path], ProcessResult]


def summary(batch: dict[str, Any]) -> str:
    count = len(batch["asks"])
    noun = "outcome" if count == 1 else "outcomes"
    return f"Seal {count} exact Denim ask {noun}"


def call_seal(
    store: Store,
    artifact: BatchArtifact,
    *,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Call `fab7 seal` once and return an exact, subject-bound receipt."""

    fab7 = _resolve_executable("fab7")
    denim = _resolve_executable("denim")
    batch = artifact.batch
    batch_id = batch["batch_id"]
    argv = [
        str(fab7),
        "seal",
        "--workspace",
        str(store.workspace),
        "--work-item",
        batch_id,
        "--summary",
        summary(batch),
        "--actor",
        ACTOR,
        "--subject-kind",
        SUBJECT_KIND,
        "--subject-ref",
        batch_id,
        "--subject-digest",
        artifact.digest,
        "--timeout",
        str(FAB7_TIMEOUT_SECONDS),
        "--json",
        "--",
        str(denim),
        "verify-batch",
        "--workspace",
        str(store.workspace),
        "--batch",
        batch_id,
    ]
    completed = (runner or _run)(argv, store.workspace)
    if completed.timed_out:
        raise DenimError(
            "DENIM_FAB7_TIMEOUT",
            "Fab7 seal timed out; every ask in the batch remains pending",
            {"batch_id": batch_id},
        )
    if completed.returncode != 0:
        context: dict[str, Any] = {
            "batch_id": batch_id,
            "exit_code": completed.returncode,
        }
        failure = _failure_json(completed.stdout)
        if failure is not None:
            context["fab7"] = failure
        raise DenimError(
            "DENIM_FAB7_FAILED",
            "Fab7 seal failed; every ask in the batch remains pending",
            context,
        )
    receipt = _receipt(completed.stdout, artifact, store)
    if not batch_is_sealed(store, artifact):
        raise DenimError(
            "DENIM_FAB7_INVALID",
            "Fab7 reported success without a matching valid sealed batch",
            {"batch_id": batch_id},
        )
    return receipt


def batch_is_sealed(store: Store, artifact: BatchArtifact) -> bool:
    """Derive sealed state from the latest valid claim and passing evidence."""

    path = store.workspace / ".fab7/records" / f"{artifact.batch['batch_id']}.jsonl"
    if not path.exists() and not path.is_symlink():
        return False
    records = _ledger(path)
    if any(record["work_item"] != artifact.batch["batch_id"] for record in records):
        raise DenimError(
            "DENIM_FAB7_INVALID", "Fab7 ledger work item does not match its path"
        )
    claims = [record for record in records if record["type"] == "claim"]
    if not claims:
        return False
    claim = claims[-1]
    expected_subject = {
        "kind": SUBJECT_KIND,
        "ref": artifact.batch["batch_id"],
        "digest": artifact.digest,
    }
    if (
        claim["work_item"] != artifact.batch["batch_id"]
        or claim["actor"] != ACTOR
        or claim["summary"] != summary(artifact.batch)
        or claim["subject"] != expected_subject
    ):
        return False
    return any(
        record["type"] == "evidence"
        and record["claim"] == claim["id"]
        and record["work_item"] == artifact.batch["batch_id"]
        and record["actor"] == ACTOR
        and record["subject_digest"] == artifact.digest
        and record["exit_code"] == 0
        for record in records
    )


def _resolve_executable(name: str) -> Path:
    candidate = shutil.which(name)
    if candidate is None:
        raise DenimError(
            "DENIM_FAB7_UNAVAILABLE", f"{name} executable is not available on PATH"
        )
    try:
        path = Path(candidate).resolve(strict=True)
    except OSError as error:
        raise DenimError(
            "DENIM_FAB7_UNAVAILABLE", f"{name} executable is unavailable"
        ) from error
    if not path.is_file() or not os.access(path, os.X_OK):
        raise DenimError(
            "DENIM_FAB7_UNAVAILABLE", f"{name} executable is not executable"
        )
    return path


def _run(argv: list[str], workspace: Path) -> ProcessResult:
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                argv,
                cwd=workspace,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
            )
        except OSError as error:
            raise DenimError(
                "DENIM_FAB7_UNAVAILABLE", "Fab7 could not be started"
            ) from error
        timed_out = False
        try:
            returncode = process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
            returncode = 124
        stdout.seek(0)
        stderr.seek(0)
        stdout_bytes = stdout.read(MAX_PROCESS_OUTPUT + 1)
        stderr_bytes = stderr.read(MAX_PROCESS_OUTPUT + 1)
    if len(stdout_bytes) > MAX_PROCESS_OUTPUT or len(stderr_bytes) > MAX_PROCESS_OUTPUT:
        raise DenimError("DENIM_FAB7_INVALID", "Fab7 output exceeds the size limit")
    return ProcessResult(returncode, stdout_bytes, stderr_bytes, timed_out)


def _receipt(content: bytes, artifact: BatchArtifact, store: Store) -> dict[str, Any]:
    try:
        payload = parse_json(content, name="Fab7 seal output")
    except DenimError as error:
        raise DenimError(
            "DENIM_FAB7_INVALID", "Fab7 seal output is not valid JSON"
        ) from error
    if (
        not isinstance(payload, dict)
        or set(payload) != _PAYLOAD_FIELDS
        or payload.get("ok") is not True
    ):
        _mismatch()
    claim = payload.get("claim")
    evidence = payload.get("evidence")
    if not _valid_claim(claim) or not _valid_evidence(evidence):
        _mismatch()
    batch_id = artifact.batch["batch_id"]
    expected_subject = {
        "kind": SUBJECT_KIND,
        "ref": batch_id,
        "digest": artifact.digest,
    }
    ledger_path = store.workspace / ".fab7/records" / f"{batch_id}.jsonl"
    if (
        claim["work_item"] != batch_id
        or claim["actor"] != ACTOR
        or claim["summary"] != summary(artifact.batch)
        or claim["subject"] != expected_subject
        or evidence["work_item"] != batch_id
        or evidence["actor"] != ACTOR
        or evidence["claim"] != claim["id"]
        or evidence["subject_digest"] != artifact.digest
        or evidence["exit_code"] != 0
        or payload.get("timed_out") is not False
        or payload.get("path") != str(ledger_path)
        or not isinstance(payload.get("lines"), list)
        or len(payload["lines"]) != 2
        or not all(type(line) is int and line > 0 for line in payload["lines"])
        or payload["lines"][1] != payload["lines"][0] + 1
    ):
        _mismatch()
    records = _ledger(ledger_path)
    claim_line, evidence_line = payload["lines"]
    if evidence_line > len(records):
        _mismatch()
    if records[claim_line - 1] != claim or records[evidence_line - 1] != evidence:
        _mismatch()
    return {
        "batch_id": batch_id,
        "batch_digest": artifact.digest,
        "claim_id": claim["id"],
        "evidence_id": evidence["id"],
        "command_digest": evidence["command_digest"],
        "output_digest": evidence["output_digest"],
        "provenance": evidence["provenance"],
    }


def _ledger(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise DenimError("DENIM_FAB7_INVALID", "Fab7 ledger is missing or invalid")
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_LEDGER_BYTES + 1)
    except OSError as error:
        raise DenimError("DENIM_FAB7_INVALID", "Fab7 ledger cannot be read") from error
    if len(content) > MAX_LEDGER_BYTES or (content and not content.endswith(b"\n")):
        raise DenimError(
            "DENIM_FAB7_INVALID", "Fab7 ledger is malformed or exceeds the size limit"
        )
    records: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    claims: set[str] = set()
    for line in content.splitlines():
        try:
            record = parse_json(line, name="Fab7 ledger record")
        except DenimError as error:
            raise DenimError(
                "DENIM_FAB7_INVALID", "Fab7 ledger contains invalid JSON"
            ) from error
        if not _valid_claim(record) and not _valid_evidence(record):
            raise DenimError("DENIM_FAB7_INVALID", "Fab7 ledger record is invalid")
        if record["id"] in identifiers:
            raise DenimError(
                "DENIM_FAB7_INVALID", "Fab7 ledger identities are not unique"
            )
        identifiers.add(record["id"])
        if record["type"] == "claim":
            claims.add(record["id"])
        elif record["claim"] not in claims:
            raise DenimError("DENIM_FAB7_INVALID", "Fab7 evidence precedes its claim")
        records.append(record)
    return records


def _valid_claim(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _CLAIM_FIELDS
        and type(value.get("v")) is int
        and value.get("v") == 1
        and value.get("type") == "claim"
        and _record_id(value.get("id"))
        and _bounded_field(value.get("work_item"), 120)
        and _bounded_field(value.get("created_at"), 128)
        and _bounded_field(value.get("actor"), 256)
        and _bounded_field(value.get("summary"), 4096)
        and _valid_subject(value.get("subject"))
    )


def _valid_evidence(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _EVIDENCE_FIELDS
        and type(value.get("v")) is int
        and value.get("v") == 1
        and value.get("type") == "evidence"
        and _record_id(value.get("id"))
        and _record_id(value.get("claim"))
        and _bounded_field(value.get("work_item"), 120)
        and _bounded_field(value.get("created_at"), 128)
        and _bounded_field(value.get("actor"), 256)
        and _digest(value.get("subject_digest"))
        and _digest(value.get("command_digest"))
        and type(value.get("exit_code")) is int
        and _digest(value.get("output_digest"))
        and _valid_provenance(value.get("provenance"))
    )


def _valid_subject(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"kind", "ref", "digest"}
        and _bounded_field(value.get("kind"), 120)
        and _bounded_field(value.get("ref"), 2048)
        and _digest(value.get("digest"))
    )


def _valid_provenance(value: Any) -> bool:
    if value == {"kind": "digest"}:
        return True
    return (
        isinstance(value, dict)
        and set(value) == {"kind", "commit"}
        and value.get("kind") == "git"
        and isinstance(value.get("commit"), str)
        and _COMMIT_RE.fullmatch(value["commit"]) is not None
    )


def _record_id(value: Any) -> bool:
    return isinstance(value, str) and _RECORD_ID_RE.fullmatch(value) is not None


def _digest(value: Any) -> bool:
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None


def _bounded_field(value: Any, limit: int) -> bool:
    return (
        isinstance(value, str) and bool(value) and len(value.encode("utf-8")) <= limit
    )


def _failure_json(content: bytes) -> dict[str, Any] | None:
    try:
        value = parse_json(content, name="Fab7 failure output")
    except DenimError:
        return None
    if not isinstance(value, dict) or value.get("ok") is not False:
        return None
    errors = value.get("errors")
    if not isinstance(errors, list) or len(errors) > 16:
        return None
    bounded: list[dict[str, str]] = []
    for error in errors:
        if (
            not isinstance(error, dict)
            or not isinstance(error.get("code"), str)
            or not isinstance(error.get("message"), str)
        ):
            return None
        bounded.append(
            {
                "code": bounded_text(error["code"], "Fab7 error code", 256),
                "message": bounded_text(error["message"], "Fab7 error message", 4096),
            }
        )
    return {"ok": False, "errors": bounded}


def _mismatch() -> None:
    raise DenimError(
        "DENIM_FAB7_INVALID", "Fab7 seal output does not match the exact Denim batch"
    )
