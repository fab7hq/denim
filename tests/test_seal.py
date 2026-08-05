from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from denim.ask import handle_ask
from denim.domain import DenimError
from denim.fab7 import ACTOR, SUBJECT_KIND, ProcessResult, summary
from denim.seal import pending_ask_ids, seal_pending, verify_batch
from denim.store import Store
from tests.test_ask import capability, prepare_request


class SealTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.home = self.root / "home"
        (self.workspace / ".fab7/records").mkdir(parents=True)
        (self.workspace / ".fab7/project.json").write_text("{}\n")
        self.home.mkdir()
        self.calls: list[list[str]] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def ask(self, result: str, *, status: str = "complete") -> str:
        prepared = handle_ask(
            self.workspace,
            prepare_request([capability("agent:native", kind="agent")]),
            home=self.home,
        )
        recorded = handle_ask(
            self.workspace,
            {
                "phase": "record",
                "ticket": prepared["ticket"],
                "status": status,
                "result": result,
                "limitations": ["authority denied"] if status == "blocked" else [],
                "effects": [],
            },
        )
        return recorded["ask_id"]

    def unavailable(self) -> str:
        outcome = handle_ask(
            self.workspace,
            prepare_request([]),
            home=self.home,
        )
        return outcome["ask_id"]

    def runner(self, argv: list[str], workspace: Path) -> ProcessResult:
        self.calls.append(argv)
        batch_id = argv[argv.index("--work-item") + 1]
        verified = verify_batch(workspace, batch_id)
        artifact = next(
            item
            for item in Store(workspace).read_batches()
            if item.batch["batch_id"] == batch_id
        )
        payload = self._seal_payload(artifact, verified["ask_count"])
        self._write_ledger(batch_id, payload)
        return ProcessResult(0, json.dumps(payload).encode(), b"")

    def _seal_payload(self, artifact: object, ask_count: int) -> dict[str, object]:
        batch = artifact.batch
        batch_id = batch["batch_id"]
        claim_id = "rec_" + batch_id.removeprefix("batch_")
        evidence_id = "rec_" + ("f" * 32 if claim_id != "rec_" + "f" * 32 else "e" * 32)
        return {
            "ok": True,
            "claim": {
                "v": 1,
                "id": claim_id,
                "type": "claim",
                "work_item": batch_id,
                "created_at": "2026-08-03T00:00:00Z",
                "actor": ACTOR,
                "summary": summary(batch),
                "subject": {
                    "kind": SUBJECT_KIND,
                    "ref": batch_id,
                    "digest": artifact.digest,
                },
            },
            "evidence": {
                "v": 1,
                "id": evidence_id,
                "type": "evidence",
                "work_item": batch_id,
                "created_at": "2026-08-03T00:00:01Z",
                "actor": ACTOR,
                "claim": claim_id,
                "subject_digest": artifact.digest,
                "command_digest": "sha256:" + "c" * 64,
                "exit_code": 0,
                "output_digest": "sha256:" + "d" * 64,
                "provenance": {"kind": "digest"},
            },
            "path": str(
                self.workspace.resolve() / ".fab7/records" / f"{batch_id}.jsonl"
            ),
            "lines": [1, 2],
            "timed_out": False,
        }

    def _write_ledger(self, batch_id: str, payload: dict[str, object]) -> None:
        path = self.workspace / ".fab7/records" / f"{batch_id}.jsonl"
        with path.open("ab") as handle:
            handle.write(json.dumps(payload["claim"], sort_keys=True).encode() + b"\n")
            handle.write(
                json.dumps(payload["evidence"], sort_keys=True).encode() + b"\n"
            )

    def seal(self) -> dict[str, object]:
        with patch(
            "denim.fab7._resolve_executable",
            side_effect=[Path("/fab7"), Path("/denim")],
        ):
            return seal_pending(self.workspace, runner=self.runner)

    def test_one_seal_selects_all_pending_in_order_and_calls_fab7_once(self) -> None:
        ask_ids = [self.ask("second"), self.ask("first")]
        receipt = self.seal()
        self.assertEqual(receipt["status"], "sealed")
        self.assertEqual(receipt["ask_count"], 2)
        self.assertEqual(len(self.calls), 1)
        argv = self.calls[0]
        self.assertEqual(argv.count("seal"), 1)
        self.assertFalse(any(token in {"sh", "bash", "zsh"} for token in argv))
        self.assertEqual(argv[0], "/fab7")
        separator = argv.index("--")
        self.assertEqual(
            argv[separator + 1 : separator + 3], ["/denim", "verify-batch"]
        )
        batch = Store(self.workspace).read_batches()[0].batch
        self.assertEqual([row["ask_id"] for row in batch["asks"]], sorted(ask_ids))
        self.assertEqual(pending_ask_ids(self.workspace), [])

    def test_prior_batch_stays_closed_and_later_ask_forms_next_batch(self) -> None:
        first = self.ask("first")
        first_receipt = self.seal()
        later = self.ask("later")
        second_receipt = self.seal()
        self.assertNotEqual(first_receipt["batch_id"], second_receipt["batch_id"])
        batches = {
            item.batch["batch_id"]: item.batch
            for item in Store(self.workspace).read_batches()
        }
        self.assertEqual(
            [row["ask_id"] for row in batches[first_receipt["batch_id"]]["asks"]],
            [first],
        )
        self.assertEqual(
            [row["ask_id"] for row in batches[second_receipt["batch_id"]]["asks"]],
            [later],
        )

    def test_failed_or_timed_out_seal_leaves_every_ask_pending(self) -> None:
        ask_id = self.ask("pending")

        def failed(argv: list[str], _workspace: Path) -> ProcessResult:
            self.calls.append(argv)
            return ProcessResult(7, b'{"ok":false,"errors":[]}', b"")

        with (
            patch(
                "denim.fab7._resolve_executable",
                side_effect=[Path("/fab7"), Path("/denim")],
            ),
            self.assertRaisesRegex(DenimError, "remains pending"),
        ):
            seal_pending(self.workspace, runner=failed)
        self.assertEqual(pending_ask_ids(self.workspace), [ask_id])

        def timed_out(argv: list[str], _workspace: Path) -> ProcessResult:
            self.calls.append(argv)
            return ProcessResult(124, b"", b"", True)

        with (
            patch(
                "denim.fab7._resolve_executable",
                side_effect=[Path("/fab7"), Path("/denim")],
            ),
            self.assertRaisesRegex(DenimError, "timed out"),
        ):
            seal_pending(self.workspace, runner=timed_out)
        self.assertEqual(pending_ask_ids(self.workspace), [ask_id])
        self.assertEqual(len(Store(self.workspace).read_batches()), 1)

    def test_success_receipt_must_name_the_exact_ledger_records(self) -> None:
        self.ask("exact receipt")

        def mismatched(argv: list[str], workspace: Path) -> ProcessResult:
            completed = self.runner(argv, workspace)
            payload = json.loads(completed.stdout)
            payload["evidence"]["id"] = "rec_" + "a" * 32
            return ProcessResult(0, json.dumps(payload).encode(), b"")

        with (
            patch(
                "denim.fab7._resolve_executable",
                side_effect=[Path("/fab7"), Path("/denim")],
            ),
            self.assertRaisesRegex(DenimError, "does not match the exact"),
        ):
            seal_pending(self.workspace, runner=mismatched)

    def test_concurrent_seal_is_rejected_without_calling_fab7(self) -> None:
        self.ask("pending")
        store = Store(self.workspace)
        store.initialize()
        descriptor = os.open(store.lock, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaisesRegex(DenimError, "Another Denim seal"):
                seal_pending(self.workspace, runner=self.runner)
        finally:
            os.close(descriptor)
        self.assertEqual(self.calls, [])

    def test_ask_arriving_after_snapshot_remains_pending(self) -> None:
        first = self.ask("first")
        later: list[str] = []

        def arriving(argv: list[str], workspace: Path) -> ProcessResult:
            later.append(self.ask("arrived while Fab7 ran"))
            return self.runner(argv, workspace)

        with patch(
            "denim.fab7._resolve_executable",
            side_effect=[Path("/fab7"), Path("/denim")],
        ):
            receipt = seal_pending(self.workspace, runner=arriving)
        self.assertEqual(receipt["ask_count"], 1)
        self.assertEqual(pending_ask_ids(self.workspace), later)
        batch = Store(self.workspace).read_batches()[0].batch
        self.assertEqual([row["ask_id"] for row in batch["asks"]], [first])

    def test_blocked_and_unavailable_statuses_are_bound_in_batch(self) -> None:
        blocked = self.ask("permission denied", status="blocked")
        unavailable = self.unavailable()
        self.seal()
        rows = {
            row["ask_id"]: row
            for row in Store(self.workspace).read_batches()[0].batch["asks"]
        }
        self.assertEqual(rows[blocked]["status"], "blocked")
        self.assertEqual(rows[unavailable]["status"], "capability_unavailable")

    def test_batch_verifier_rejects_tampered_record_or_prior_overlap(self) -> None:
        ask_id = self.ask("exact")

        def failed(argv: list[str], _workspace: Path) -> ProcessResult:
            return ProcessResult(1, b"", b"")

        with (
            patch(
                "denim.fab7._resolve_executable",
                side_effect=[Path("/fab7"), Path("/denim")],
            ),
            self.assertRaises(DenimError),
        ):
            seal_pending(self.workspace, runner=failed)
        artifact = Store(self.workspace).read_batches()[0]
        record_path = self.workspace / ".fab7/denim/asks" / f"{ask_id}.json"
        record = json.loads(record_path.read_text())
        record["effects"] = ["forged effect"]
        record_path.write_text(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        )
        with self.assertRaises(DenimError):
            verify_batch(self.workspace, artifact.batch["batch_id"])

    def test_empty_workspace_does_not_call_fab7(self) -> None:
        result = seal_pending(self.workspace, runner=self.runner)
        self.assertEqual(
            result, {"ok": True, "status": "nothing_to_seal", "ask_count": 0}
        )
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
