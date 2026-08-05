from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from denim.cli import _parser


class ExtensionTest(unittest.TestCase):
    def test_source_contains_only_replacement_operations_and_no_roles(self) -> None:
        self.assertEqual(
            sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir()),
            ["ask", "seal"],
        )
        self.assertFalse((ROOT / "agents").exists())
        self.assertEqual(
            sorted(path.name for path in (ROOT / "src/denim").glob("*.py")),
            [
                "__init__.py",
                "ask.py",
                "cli.py",
                "config.py",
                "discovery.py",
                "domain.py",
                "fab7.py",
                "seal.py",
                "store.py",
            ],
        )

    def test_public_cli_exposes_only_ask_and_seal(self) -> None:
        help_text = _parser().format_help()
        self.assertIn("{ask,seal}", help_text)
        self.assertNotIn("verify-batch", help_text)

    def test_host_neutral_skills_are_explicit_only_and_share_semantics(self) -> None:
        ask = (ROOT / "skills/ask/SKILL.md").read_text()
        seal = (ROOT / "skills/seal/SKILL.md").read_text()
        for source in (ask, seal):
            self.assertIn("{{invocation}}", source)
            self.assertIn("disable-model-invocation: true", source)
        for name in ("ask", "seal"):
            metadata = (ROOT / f"skills/{name}/agents/openai.yaml").read_text()
            self.assertIn("allow_implicit_invocation: false", metadata)
        self.assertIn("exactly once", ask)
        self.assertIn("owner-, user-, or workspace-authored", ask)
        self.assertIn("plugin whose", ask)
        self.assertIn("Treat `*` only as generic fallback", ask)
        self.assertIn("one atomic", ask)
        self.assertIn("separate explicit\nDenim `seal` skill", ask)
        self.assertIn("exactly once", seal)
        self.assertIn("not proof", seal)

        protocol = (ROOT / "skills/ask/references/protocol.md").read_text()
        self.assertIn("preference and ownership class", protocol)
        self.assertIn("does not establish owner", protocol)
        self.assertIn("one invocation and one final result", protocol)
        self.assertIn("only when no eligible candidate has a specific", protocol)

    def test_canonical_entrypoint_has_bounded_help(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "src"
            shutil.copytree(
                ROOT / "src",
                copied,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
            completed = subprocess.run(
                [sys.executable, str(copied / "extension.py"), "--help"],
                cwd=directory,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("{ask,seal}", completed.stdout)
            self.assertFalse(any(copied.rglob("__pycache__")))


if __name__ == "__main__":
    unittest.main()
