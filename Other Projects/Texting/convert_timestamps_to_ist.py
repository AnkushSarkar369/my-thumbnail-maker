#!/usr/bin/env python3
"""Convert normalized Instagram/WhatsApp archive timestamps to explicit IST.

Run this from the directory containing I_Simple.json, W_Old.json, and
W_New.json. Original files are backed up once as *.before-ist.bak.
Use --dry-run to preview counts without writing anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

FILES = ("I_Simple.json", "W_Old.json", "W_New.json")
IST = ZoneInfo("Asia/Kolkata")
NUMERIC = re.compile(r"-?\d+(?:\.\d+)?\Z")


def epoch_datetime(value: float) -> datetime:
    # Match Reality.py's epoch convention: large values are milliseconds.
    if abs(value) >= 100_000_000_000:
        value /= 1000
    return datetime.fromtimestamp(value, tz=timezone.utc)


def to_ist_iso(value: object) -> tuple[object, str]:
    """Return (replacement, status); leave ambiguous/invalid values untouched."""
    dt = None
    if isinstance(value, bool) or value is None:
        return value, "skipped"
    if isinstance(value, (int, float)):
        try:
            dt = epoch_datetime(float(value))
        except (OverflowError, OSError, ValueError):
            return value, "skipped"
    elif isinstance(value, str):
        raw = value.strip()
        if NUMERIC.fullmatch(raw):
            try:
                dt = epoch_datetime(float(raw))
            except (OverflowError, OSError, ValueError):
                return value, "skipped"
        else:
            normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return value, "skipped"
            # A naive timestamp has no known timezone. Do not guess or shift it.
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return value, "timezone-ambiguous"
            dt = parsed

    if dt is None:
        return value, "skipped"

    localized = dt.astimezone(IST)
    timespec = "microseconds" if localized.microsecond else "seconds"
    replacement = localized.isoformat(timespec=timespec)
    return replacement, "unchanged" if replacement == value else "converted"


def get_records(data: object) -> list | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        records = data.get("messages", data.get("events"))
        if isinstance(records, list):
            return records
    return None


def infer_indent(raw: str) -> int | None:
    if "\n" not in raw:
        return None
    match = re.search(r"\n([ \t]+)\S", raw)
    return len(match.group(1)) if match else 2


def convert_file(path: Path, dry_run: bool) -> bool:
    if not path.is_file():
        print(f"{path.name}: not found")
        return False

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"{path.name}: could not read valid JSON ({exc}); untouched")
        return False

    records = get_records(data)
    if records is None:
        print(f"{path.name}: no list of records found; untouched")
        return False

    converted = 0
    already_ist = 0
    skipped = 0
    ambiguous = 0
    for record in records:
        if not isinstance(record, dict) or "timestamp" not in record:
            skipped += 1
            continue
        old = record["timestamp"]
        new, status = to_ist_iso(old)
        if status == "converted":
            record["timestamp"] = new
            converted += 1
        elif status == "unchanged":
            # It was already represented as the same explicit IST timestamp.
            already_ist += 1
        elif status == "timezone-ambiguous":
            ambiguous += 1
        else:
            skipped += 1

    backup = path.with_name(path.name + ".before-ist.bak")
    if dry_run:
        print(
            f"{path.name}: would convert {converted:,}; already IST {already_ist:,}; "
            f"timezone-ambiguous {ambiguous:,}; skipped {skipped:,}"
        )
        return True

    try:
        # Keep the first pristine backup. Never overwrite it on repeated runs.
        if not backup.exists():
            with backup.open("xb") as handle:
                handle.write(raw.encode("utf-8"))

        indent = infer_indent(raw)
        fd, temporary_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                if indent is None:
                    json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
                else:
                    json.dump(data, handle, ensure_ascii=False, indent=indent)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        print(f"{path.name}: write failed ({exc}); check backup and permissions")
        return False

    print(
        f"{path.name}: converted {converted:,}; already IST {already_ist:,}; "
        f"timezone-ambiguous {ambiguous:,}; skipped {skipped:,}; "
        f"backup: {backup.name}"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert WhatsApp/Instagram JSON timestamps to explicit IST."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="show conversion counts without changing any files"
    )
    parser.add_argument(
        "--directory", type=Path, default=Path.cwd(),
        help="directory containing the three JSON archives (default: current directory)"
    )
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve()

    print(f"Directory: {directory}")
    print("Timestamp format: ISO 8601 with +05:30 (IST)")
    if args.dry_run:
        print("DRY RUN — no files will be changed")
    else:
        print("Original files will be backed up once as *.before-ist.bak")
    print()

    results = [
        convert_file(directory / filename, args.dry_run)
        for filename in FILES
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
