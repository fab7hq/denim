from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from denim.ask import handle_ask
from denim.domain import DenimError
from denim.store import Store


def capability(
    identifier: str,
    *,
    host: str = "codex",
    origin: str = "builtin",
    provides: list[str] | None = None,
    visible: bool = True,
    callable: bool = True,
    result_capture: bool = True,
    authority: list[str] | None = None,
    priority: int = 0,
    kind: str = "tool",
) -> dict[str, object]:
    return {
        "id": identifier,
        "host": host,
        "origin": origin,
        "kind": kind,
        "description": f"Live {identifier} capability",
        "provides": provides or ["*"],
        "visible": visible,
        "callable": callable,
        "result_capture": result_capture,
        "required_authority": authority or [],
        "priority": priority,
    }


def prepare_request(
    candidates: list[dict[str, object]],
    *,
    prompt: str = "Review this change; do not edit files.",
    authority: list[str] | None = None,
    prohibitions: list[str] | None = None,
    route: str | None = None,
    use: str | None = None,
) -> dict[str, object]:
    return {
        "phase": "prepare",
        "host": "codex",
        "prompt": prompt,
        "candidates": candidates,
        "authority": authority or [],
        "prohibitions": prohibitions or ["do not edit files"],
        "target": "current workspace",
        "route": route,
        "use": use,
    }


class AskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.home = self.root / "home"
        (self.workspace / ".fab7").mkdir(parents=True)
        (self.workspace / ".fab7/project.json").write_text("{}\n")
        self.home.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_each_ask_uses_fresh_discovery_and_custom_wins_by_default(self) -> None:
        first = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "skill:custom",
                        origin="custom",
                        provides=["review"],
                    ),
                    capability(
                        "api:builtin", origin="builtin", provides=["review"]
                    ),
                ]
            ),
            home=self.home,
        )
        second = handle_ask(
            self.workspace,
            prepare_request([capability("tool:new-live-builtin")]),
            home=self.home,
        )
        self.assertEqual(first["capability"]["id"], "skill:custom")
        self.assertEqual(second["capability"]["id"], "tool:new-live-builtin")
        self.assertNotEqual(first["ask_id"], second["ask_id"])

    def test_unrelated_and_wildcard_custom_do_not_shadow_specific_builtin(self) -> None:
        for custom_provides in (["database-migration"], ["*"]):
            with self.subTest(custom_provides=custom_provides):
                selected = handle_ask(
                    self.workspace,
                    prepare_request(
                        [
                            capability(
                                "skill:custom",
                                origin="custom",
                                provides=custom_provides,
                                priority=10_000,
                            ),
                            capability(
                                "api:builtin",
                                origin="builtin",
                                provides=["review"],
                                priority=-10_000,
                            ),
                        ]
                    ),
                    home=self.home,
                )
                self.assertEqual(selected["capability"]["id"], "api:builtin")

    def test_builtin_wins_when_no_relevant_custom_capability_is_live(self) -> None:
        selected = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "skill:unrelated",
                        origin="custom",
                        provides=["deployment"],
                    ),
                    capability(
                        "tool:review", origin="builtin", provides=["review"]
                    ),
                ]
            ),
            home=self.home,
        )
        self.assertEqual(selected["capability"]["id"], "tool:review")

    def test_wildcard_is_used_only_when_no_specific_match_exists(self) -> None:
        selected = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "skill:generic-custom",
                        origin="custom",
                        provides=["*"],
                    ),
                    capability(
                        "agent:native", origin="fallback", provides=["*"]
                    ),
                ],
                prompt="Explain this unfamiliar topic.",
            ),
            home=self.home,
        )
        self.assertEqual(selected["capability"]["id"], "skill:generic-custom")

        mixed = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "skill:mixed",
                        origin="custom",
                        provides=["*", "security-review"],
                    ),
                    capability(
                        "tool:review", origin="builtin", provides=["security-review"]
                    ),
                ],
                prompt="Perform a security review.",
            ),
            home=self.home,
        )
        self.assertEqual(mixed["capability"]["id"], "skill:mixed")

    def test_ineligible_custom_never_bypasses_any_eligibility_gate(self) -> None:
        cases = {
            "visibility": {"visible": False},
            "callability": {"callable": False},
            "result capture": {"result_capture": False},
            "authority": {"authority": ["write"]},
        }
        for label, overrides in cases.items():
            with self.subTest(gate=label):
                custom = capability(
                    "skill:custom",
                    origin="custom",
                    provides=["review"],
                    **overrides,
                )
                selected = handle_ask(
                    self.workspace,
                    prepare_request(
                        [
                            custom,
                            capability(
                                "tool:builtin",
                                origin="builtin",
                                provides=["review"],
                            ),
                        ]
                    ),
                    home=self.home,
                )
                self.assertEqual(selected["capability"]["id"], "tool:builtin")

    def test_origin_is_host_asserted_and_ambiguous_plugin_is_not_promoted(self) -> None:
        selected = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "plugin:third-party",
                        origin="plugin",
                        provides=["review"],
                        priority=10_000,
                    ),
                    capability(
                        "tool:builtin", origin="builtin", provides=["review"]
                    ),
                ]
            ),
            home=self.home,
        )
        self.assertEqual(selected["capability"]["id"], "tool:builtin")

        owner_authored = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "plugin:owner-workflow",
                        origin="custom",
                        provides=["review"],
                        kind="skill",
                    ),
                    capability(
                        "tool:builtin", origin="builtin", provides=["review"]
                    ),
                ]
            ),
            home=self.home,
        )
        self.assertEqual(
            owner_authored["capability"]["id"], "plugin:owner-workflow"
        )

        forged = capability(
            "plugin:forged", origin="plugin", provides=["review"]
        )
        forged["owner_provenance"] = "workspace"
        with self.assertRaisesRegex(DenimError, "descriptor fields are invalid"):
            handle_ask(
                self.workspace,
                prepare_request([forged]),
                home=self.home,
            )

    def test_exact_use_retains_eligible_pin_semantics(self) -> None:
        selected = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "skill:custom", origin="custom", provides=["review"]
                    ),
                    capability(
                        "tool:builtin", origin="builtin", provides=["review"]
                    ),
                ],
                use="tool:builtin",
            ),
            home=self.home,
        )
        self.assertEqual(selected["capability"]["id"], "tool:builtin")

        unavailable = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "skill:custom", origin="custom", provides=["review"]
                    )
                ],
                use="tool:absent",
            ),
            home=self.home,
        )
        self.assertEqual(unavailable["status"], "capability_unavailable")

    def test_explicit_builtin_first_order_overrides_default(self) -> None:
        (self.workspace / ".denim.toml").write_text(
            """version = 1
[resolution]
order = ["builtin", "custom", "plugin", "fallback"]
"""
        )
        selected = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "skill:custom", origin="custom", provides=["review"]
                    ),
                    capability(
                        "tool:builtin", origin="builtin", provides=["review"]
                    ),
                ]
            ),
            home=self.home,
        )
        self.assertEqual(selected["capability"]["id"], "tool:builtin")

    def test_missing_configured_custom_is_not_synthesized(self) -> None:
        (self.workspace / ".denim.toml").write_text(
            """version = 1
[routes.review]
use = "skill:absent-custom"
allow_fallback = true
[[capabilities]]
id = "skill:absent-custom"
provides = ["review"]
priority = 10000
"""
        )
        selected = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "tool:live-builtin",
                        origin="builtin",
                        provides=["review"],
                    )
                ],
                route="review",
            ),
            home=self.home,
        )
        self.assertEqual(selected["capability"]["id"], "tool:live-builtin")

    def test_match_then_priority_then_id_break_ties_within_origin(self) -> None:
        selected = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "skill:weak-high-priority",
                        origin="custom",
                        provides=["review"],
                        priority=10_000,
                    ),
                    capability(
                        "skill:strong",
                        origin="custom",
                        provides=["security-review"],
                    ),
                    capability(
                        "skill:strong-low-priority",
                        origin="custom",
                        provides=["security-review"],
                        priority=-1,
                    ),
                ],
                prompt="Perform a security review.",
            ),
            home=self.home,
        )
        self.assertEqual(selected["capability"]["id"], "skill:strong")

        lexical = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "skill:z", origin="custom", provides=["review"]
                    ),
                    capability(
                        "skill:a", origin="custom", provides=["review"]
                    ),
                ]
            ),
            home=self.home,
        )
        self.assertEqual(lexical["capability"]["id"], "skill:a")

    def test_valid_configured_pin_overrides_without_creating_capability(self) -> None:
        (self.workspace / ".denim.toml").write_text(
            """version = 1

[routes.security_review]
use = "skill:custom"
allow_fallback = false
"""
        )
        selected = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability("api:builtin", provides=["security_review"]),
                    capability(
                        "skill:custom",
                        origin="custom",
                        provides=["security_review"],
                    ),
                ],
                route="security_review",
            ),
            home=self.home,
        )
        self.assertEqual(selected["capability"]["id"], "skill:custom")

        unavailable = handle_ask(
            self.workspace,
            prepare_request(
                [capability("api:builtin", provides=["security_review"])],
                route="security_review",
            ),
            home=self.home,
        )
        self.assertEqual(unavailable["status"], "capability_unavailable")
        self.assertEqual(unavailable["capability_id"], "unavailable")

    def test_ineligible_candidates_fail_closed(self) -> None:
        candidates = [
            capability("command:ui-only", kind="command", callable=False),
            capability("tool:no-result", result_capture=False),
            capability("skill:hidden", visible=False),
            capability("agent:admin", authority=["write"]),
        ]
        outcome = handle_ask(
            self.workspace,
            prepare_request(candidates),
            home=self.home,
        )
        self.assertEqual(outcome["status"], "capability_unavailable")
        record = Store(self.workspace).read_asks()[0]
        self.assertEqual(record.record["status"], "capability_unavailable")
        self.assertIn("No eligible", record.result)

    def test_prompt_preserves_exact_ask_and_prohibitions(self) -> None:
        prompt = "Line one\nLine two: do not expose credentials."
        prepared = handle_ask(
            self.workspace,
            prepare_request(
                [capability("agent:native", kind="agent")],
                prompt=prompt,
                prohibitions=["do not expose credentials", "do not edit"],
            ),
            home=self.home,
        )
        enriched = prepared["enriched_prompt"]
        self.assertIn(f"Exact ask: {prompt}", enriched)
        self.assertIn("do not expose credentials; do not edit", enriched)
        self.assertEqual(prepared["ticket"]["prompt"], prompt)

    def test_route_must_be_a_bounded_token(self) -> None:
        with self.assertRaisesRegex(DenimError, "bounded lowercase token"):
            handle_ask(
                self.workspace,
                prepare_request([capability("tool:review")], route="../review"),
                home=self.home,
            )

    def test_one_selected_capability_records_one_exact_immutable_result(self) -> None:
        prepared = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability("tool:first", priority=10),
                    capability("tool:second"),
                ]
            ),
            home=self.home,
        )
        result = "Exact delegated result\nincluding the final newline.\n"
        recorded = handle_ask(
            self.workspace,
            {
                "phase": "record",
                "ticket": prepared["ticket"],
                "status": "complete",
                "result": result,
                "limitations": [],
                "effects": ["read workspace"],
            },
        )
        self.assertEqual(recorded["capability_id"], "tool:first")
        asks = Store(self.workspace).read_asks()
        self.assertEqual(len(asks), 1)
        self.assertEqual(asks[0].result, result)
        self.assertEqual(asks[0].record["prompt"], prepared["ticket"]["prompt"])

        with self.assertRaisesRegex(DenimError, "already exists"):
            handle_ask(
                self.workspace,
                {
                    "phase": "record",
                    "ticket": prepared["ticket"],
                    "status": "complete",
                    "result": result,
                    "limitations": [],
                    "effects": [],
                },
            )

    def test_custom_workflow_is_one_ticket_with_only_its_final_result(self) -> None:
        prepared = handle_ask(
            self.workspace,
            prepare_request(
                [
                    capability(
                        "skill:owner-workflow",
                        origin="custom",
                        provides=["review"],
                        kind="skill",
                    )
                ]
            ),
            home=self.home,
        )
        self.assertEqual(prepared["capability"]["kind"], "skill")
        handle_ask(
            self.workspace,
            {
                "phase": "record",
                "ticket": prepared["ticket"],
                "status": "complete",
                "result": "bounded final workflow result",
                "limitations": ["live provenance supplied by host"],
                "effects": ["read workspace"],
            },
        )
        artifact = Store(self.workspace).read_asks()[0]
        self.assertEqual(artifact.result, "bounded final workflow result")
        self.assertNotIn("steps", artifact.record)
        self.assertNotIn("transcript", artifact.record)

    def test_ask_runtime_never_calls_fab7_or_any_subprocess(self) -> None:
        with patch(
            "subprocess.Popen", side_effect=AssertionError("unexpected process")
        ):
            prepared = handle_ask(
                self.workspace,
                prepare_request([capability("tool:review")]),
                home=self.home,
            )
            handle_ask(
                self.workspace,
                {
                    "phase": "record",
                    "ticket": prepared["ticket"],
                    "status": "complete",
                    "result": "captured host result",
                    "limitations": [],
                    "effects": [],
                },
            )
        self.assertEqual(len(Store(self.workspace).read_asks()), 1)

    def test_tampered_result_fails_digest_validation(self) -> None:
        prepared = handle_ask(
            self.workspace,
            prepare_request([capability("tool:review")]),
            home=self.home,
        )
        handle_ask(
            self.workspace,
            {
                "phase": "record",
                "ticket": prepared["ticket"],
                "status": "blocked",
                "result": "Permission denied",
                "limitations": ["write authority was not granted"],
                "effects": [],
            },
        )
        result_path = next((self.workspace / ".fab7/denim/asks").glob("*.result"))
        result_path.write_text("forged")
        with self.assertRaisesRegex(DenimError, "digest binding"):
            Store(self.workspace).read_asks()

    def test_ask_state_stays_below_nested_runtime_boundary(self) -> None:
        before = sorted(
            path.relative_to(self.workspace).as_posix()
            for path in self.workspace.rglob("*")
            if path.is_file()
        )
        handle_ask(
            self.workspace,
            prepare_request([]),
            home=self.home,
        )
        after = sorted(
            path.relative_to(self.workspace).as_posix()
            for path in self.workspace.rglob("*")
            if path.is_file()
            and ".fab7/denim/" not in path.relative_to(self.workspace).as_posix()
        )
        self.assertEqual(before, after)
        self.assertEqual((self.workspace / ".fab7/denim/.gitignore").read_text(), "*\n")
        self.assertFalse(
            any(path.name == "__pycache__" for path in self.workspace.rglob("*"))
        )


if __name__ == "__main__":
    unittest.main()
