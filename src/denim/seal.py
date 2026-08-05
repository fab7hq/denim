"""Exclusive pending-set snapshots and explicit Fab7 sealing."""

from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .domain import BATCH_ID_RE, DenimError, new_batch_id
from .fab7 import Runner, batch_is_sealed, call_seal
from .store import BatchArtifact, Store, batch_from_asks


def seal_pending(workspace: Path, *, runner: Runner | None = None) -> dict[str, Any]:
    store = Store(workspace)
    store.initialize()
    with _seal_lock(store):
        asks = store.read_asks()
        batches = store.read_batches()
        sealed = _sealed_ask_ids(store, batches, asks)
        pending = [item for item in asks if item.record["ask_id"] not in sealed]
        if not pending:
            return {"ok": True, "status": "nothing_to_seal", "ask_count": 0}
        artifact = _reusable_batch(batches, pending, store)
        if artifact is None:
            artifact = store.write_batch(batch_from_asks(new_batch_id(), pending))
        receipt = call_seal(store, artifact, runner=runner)
        return {
            "ok": True,
            "status": "sealed",
            "ask_count": len(artifact.batch["asks"]),
            **receipt,
        }


def verify_batch(workspace: Path, batch_id: str) -> dict[str, Any]:
    """Verify one immutable batch while the parent seal owns the workspace lock."""

    if BATCH_ID_RE.fullmatch(batch_id) is None:
        raise DenimError("DENIM_INVALID_INPUT", "Batch identity is invalid")
    store = Store(workspace)
    batches = store.read_batches()
    artifact = next(
        (item for item in batches if item.batch["batch_id"] == batch_id), None
    )
    if artifact is None:
        raise DenimError("DENIM_BATCH_UNKNOWN", "Batch receipt does not exist")
    asks = {item.record["ask_id"]: item for item in store.read_asks()}
    for row in artifact.batch["asks"]:
        ask = asks.get(row["ask_id"])
        if ask is None or row != {
            "ask_id": ask.record["ask_id"],
            "record_digest": ask.record_digest,
            "result_digest": ask.record["result_digest"],
            "status": ask.record["status"],
        }:
            raise DenimError(
                "DENIM_BATCH_INVALID",
                "Batch ask binding does not match immutable state",
            )
    prior = _sealed_ask_ids(
        store,
        [item for item in batches if item.batch["batch_id"] != batch_id],
        list(asks.values()),
    )
    overlap = sorted(
        row["ask_id"] for row in artifact.batch["asks"] if row["ask_id"] in prior
    )
    if overlap:
        raise DenimError(
            "DENIM_BATCH_INVALID",
            "Batch includes asks already sealed by a prior batch",
            {"ask_ids": overlap},
        )
    return {
        "ok": True,
        "status": "verified",
        "batch_id": batch_id,
        "batch_digest": artifact.digest,
        "ask_count": len(artifact.batch["asks"]),
    }


def pending_ask_ids(workspace: Path) -> list[str]:
    store = Store(workspace)
    asks = store.read_asks()
    sealed = _sealed_ask_ids(store, store.read_batches(), asks)
    return sorted(
        item.record["ask_id"] for item in asks if item.record["ask_id"] not in sealed
    )


def _sealed_ask_ids(
    store: Store,
    batches: list[BatchArtifact],
    asks: list[Any],
) -> set[str]:
    known = {item.record["ask_id"]: item for item in asks}
    sealed: set[str] = set()
    for artifact in batches:
        if not batch_is_sealed(store, artifact):
            continue
        overlap = sealed.intersection(row["ask_id"] for row in artifact.batch["asks"])
        if overlap:
            raise DenimError(
                "DENIM_STATE_INVALID",
                "Valid sealed batches contain the same ask more than once",
                {"ask_ids": sorted(overlap)},
            )
        for row in artifact.batch["asks"]:
            ask = known.get(row["ask_id"])
            expected = (
                {
                    "ask_id": ask.record["ask_id"],
                    "record_digest": ask.record_digest,
                    "result_digest": ask.record["result_digest"],
                    "status": ask.record["status"],
                }
                if ask is not None
                else None
            )
            if row != expected:
                raise DenimError(
                    "DENIM_STATE_INVALID",
                    "Sealed batch no longer matches its immutable ask artifacts",
                )
            sealed.add(row["ask_id"])
    return sealed


def _reusable_batch(
    batches: list[BatchArtifact],
    pending: list[Any],
    store: Store,
) -> BatchArtifact | None:
    expected = batch_from_asks("batch_" + "0" * 32, pending)["asks"]
    for artifact in reversed(batches):
        if artifact.batch["asks"] == expected and not batch_is_sealed(store, artifact):
            return artifact
    return None


@contextmanager
def _seal_lock(store: Store) -> Iterator[None]:
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(store.lock, flags, 0o600)
    except OSError as error:
        raise DenimError(
            "DENIM_SEAL_LOCKED", "Workspace seal lock is unavailable"
        ) from error
    try:
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise DenimError(
                    "DENIM_SEAL_LOCKED", "Workspace seal lock is not a regular file"
                )
            os.fchmod(descriptor, 0o600)
        except OSError as error:
            raise DenimError(
                "DENIM_SEAL_LOCKED", "Workspace seal lock cannot be inspected"
            ) from error
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DenimError(
                "DENIM_SEAL_LOCKED",
                "Another Denim seal is active for this workspace",
            ) from error
        yield
    finally:
        os.close(descriptor)
