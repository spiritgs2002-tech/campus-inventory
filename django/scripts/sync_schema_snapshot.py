#!/usr/bin/env python3
"""Check current SQL fingerprints and snapshot; --write syncs from the source only.

The published sql/schema-v7.sql and migration deltas are never rewritten.
No database access or third-party dependencies are required.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re


BODY_START = re.compile(rb"^BEGIN;[ \t]*(?:\r?\n|$)", re.MULTILINE)
FINGERPRINT = re.compile(
    r"(SHA-256\(SQL 正文，自 BEGIN 语句起\): )([0-9a-f]{64})".encode()
)
LINE_COUNT = re.compile(r"(SQL 正文行数: )([0-9]+)".encode())


def refresh_header(raw):
    start = BODY_START.search(raw)
    if start is None:
        raise ValueError("SCHEMA_BODY_MISSING: expected a BEGIN; statement line")
    header, body = raw[: start.start()], raw[start.start() :]
    if len(FINGERPRINT.findall(header)) != 1 or len(LINE_COUNT.findall(header)) != 1:
        raise ValueError("SCHEMA_HEADER_INVALID: fingerprint and line count required")
    digest = hashlib.sha256(body).hexdigest()
    lines = len(body.splitlines())
    header = FINGERPRINT.sub(lambda match: match[1] + digest.encode(), header)
    header = LINE_COUNT.sub(lambda match: match[1] + str(lines).encode(), header)
    return header + body, digest, lines


def inspect_snapshot(app_root, *, write=False):
    app_root = Path(app_root).resolve()
    source = app_root.parent / "schema-v7.sql"
    snapshot = app_root / "sql/schema-v7-current.sql"
    if write and not source.is_file():
        raise ValueError(
            "SCHEMA_SOURCE_MISSING: --write requires the authoritative source"
        )
    canonical = source if source.is_file() else snapshot
    raw = canonical.read_bytes()
    refreshed, digest, lines = refresh_header(raw)
    if write:
        if raw != refreshed:
            source.write_bytes(refreshed)
        if not snapshot.is_file() or snapshot.read_bytes() != refreshed:
            snapshot.write_bytes(refreshed)
    elif raw != refreshed:
        raise ValueError(f"SCHEMA_FINGERPRINT_STALE: {canonical}")
    if not snapshot.is_file() or snapshot.read_bytes() != refreshed:
        raise ValueError(f"SCHEMA_SNAPSHOT_DRIFT: {snapshot}")
    return {
        "authoritative_source": str(source) if source.is_file() else None,
        "packaged_snapshot": str(snapshot),
        "body_sha256": digest,
        "body_lines": lines,
        "fingerprint_valid": True,
        "snapshot_matches": True,
        "published_migrations_rewritten": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only check (default)")
    mode.add_argument(
        "--write", action="store_true", help="Refresh source header and snapshot"
    )
    args = parser.parse_args()
    try:
        result = inspect_snapshot(Path(__file__).resolve().parents[1], write=args.write)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
