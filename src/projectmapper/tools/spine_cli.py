"""Minimal CLI for spine operations.

Commands
--------
  init    <path>                           create a new empty spine
  register <path> --schema <file.json>     append a schema definition
  append  <path> --schema <id@version> --data <file.json>   append an entry
  read    <path> [--kind entry|schema|all] print matching records as JSONL

All writes use spine.py directly.  Errors go to stderr; exit 1 on failure.
"""

import argparse
import json
import sys
from pathlib import Path

from .spine import Spine, SpineError


def _die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def _read_json_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        _die(f"file not found: {path}")
        raise AssertionError("unreachable")
    except json.JSONDecodeError as exc:
        _die(f"invalid JSON in {path}: {exc}")
        raise AssertionError("unreachable")
    return data


def cmd_init(args) -> None:
    path = Path(args.path)
    try:
        Spine.create(path)
    except SpineError as exc:
        _die(str(exc))
        return
    print(f"created: {path}", file=sys.stderr)


def cmd_register(args) -> None:
    path = Path(args.path)
    schema_def = _read_json_file(Path(args.schema))
    try:
        spine = Spine.open(path)
        key = spine.register_schema(schema_def)
    except SpineError as exc:
        _die(str(exc))
        return
    print(f"registered: {key}", file=sys.stderr)


def cmd_append(args) -> None:
    path = Path(args.path)
    data = _read_json_file(Path(args.data))
    try:
        spine = Spine.open(path)
        spine.append_entry(args.schema, data)
    except SpineError as exc:
        _die(str(exc))
        return
    print(f"appended: {args.schema}", file=sys.stderr)


def cmd_read(args) -> None:
    path = Path(args.path)
    try:
        spine = Spine.open(path)
    except SpineError as exc:
        _die(str(exc))
        return
    kind = args.kind
    if kind == "entry":
        records = spine.read_entries()
    elif kind == "schema":
        records = [r for r in spine.read_all() if r.get("kind") == "schema"]
    else:
        records = spine.read_all()
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spine-cli",
        description="Create and read single-file append-only spine files.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create a new empty spine")
    p_init.add_argument("path", help="path to the .jsonl file to create")

    p_reg = sub.add_parser("register", help="append a schema definition")
    p_reg.add_argument("path", help="path to the spine .jsonl file")
    p_reg.add_argument("--schema", required=True, metavar="FILE",
                       help="JSON file containing the schema definition")

    p_app = sub.add_parser("append", help="append an entry")
    p_app.add_argument("path", help="path to the spine .jsonl file")
    p_app.add_argument("--schema", required=True, metavar="ID@VERSION",
                       help="schema key (e.g. design-event@1)")
    p_app.add_argument("--data", required=True, metavar="FILE",
                       help="JSON file containing the entry data")

    p_read = sub.add_parser("read", help="print records as JSONL to stdout")
    p_read.add_argument("path", help="path to the spine .jsonl file")
    p_read.add_argument("--kind", choices=["entry", "schema", "all"], default="all",
                        help="filter by record kind (default: all)")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "init": cmd_init,
        "register": cmd_register,
        "append": cmd_append,
        "read": cmd_read,
    }
    try:
        dispatch[args.command](args)
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
