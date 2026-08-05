"""Public `ask` and `seal` commands plus the hidden batch verifier."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import VERSION
from .ask import handle_ask
from .domain import MAX_JSON_BYTES, DenimError, parse_json
from .seal import seal_pending, verify_batch


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["verify-batch"]:
        return _verify(arguments[1:])
    parser = _parser()
    try:
        args = parser.parse_args(arguments)
        if args.command == "ask":
            request = _request(args.request)
            result = handle_ask(args.workspace, request)
        elif args.command == "seal":
            result = seal_pending(args.workspace)
        else:
            parser.error("a command is required")
            return 2
        _print(result, args.json)
        return 0
    except DenimError as error:
        _print({"ok": False, "error": error.to_dict()}, True)
        return 1
    except KeyboardInterrupt:
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="denim", description="Delegate exact asks and seal their batches"
    )
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command")
    ask = commands.add_parser("ask", help="prepare or record one exact host-native ask")
    ask.add_argument("--workspace", type=Path, default=Path.cwd())
    ask.add_argument("--request", type=Path, required=True)
    ask.add_argument("--json", action="store_true")
    seal = commands.add_parser("seal", help="seal every pending workspace ask once")
    seal.add_argument("--workspace", type=Path, default=Path.cwd())
    seal.add_argument("--json", action="store_true")
    return parser


def _verify(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="denim verify-batch", add_help=False)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    try:
        args = parser.parse_args(argv)
        print(
            json.dumps(
                verify_batch(args.workspace, args.batch),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except DenimError as error:
        print(
            json.dumps(
                {"ok": False, "error": error.to_dict()},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


def _request(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise DenimError("DENIM_INVALID_INPUT", "Ask request must be a regular file")
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_JSON_BYTES + 1)
    except OSError as error:
        raise DenimError("DENIM_INVALID_INPUT", "Ask request cannot be read") from error
    if len(content) > MAX_JSON_BYTES:
        raise DenimError("DENIM_BOUNDS", "Ask request exceeds the size limit")
    return parse_json(content, name="Ask request")


def _print(value: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
        return
    if value.get("status") == "ready":
        print(f"Ask: {value['ask_id']}")
        capability = value.get("capability")
        if isinstance(capability, dict):
            print(f"Capability: {capability['id']}")
        print("Status: ready")
        print("Seal: pending")
    elif "ask_id" in value:
        print(f"Ask: {value['ask_id']}")
        print(f"Capability: {value['capability_id']}")
        print(f"Status: {value['status']}")
        print("Seal: pending")
    elif value.get("status") == "nothing_to_seal":
        print("Seal: nothing_to_seal")
    else:
        print(f"Batch: {value['batch_id']}")
        print(f"Asks: {value['ask_count']}")
        print("Seal: sealed")
