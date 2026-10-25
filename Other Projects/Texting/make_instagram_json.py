#!/usr/bin/env python3
"""Merge Instagram's message_*.json export chunks into a compact Instagram.json.

Run from the folder containing message_1.json, message_2.json, etc.:
    python3 make_instagram_json.py --dry-run
    python3 make_instagram_json.py

Source exports are never modified. All dictionary records are retained, sorted
oldest-first, and not deduplicated. Output is valid UTF-8 JSON, with one message
object per line to keep it compact without making it one enormous line.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
MOJIBAKE_MARKERS = (
    "ð", "Ã", "Â", "â€", "â€™", "â€œ", "ï»¿", "\ufffd",
    "ê§", "à¼", "â¢",
)
INTERNAL_FIELDS = {"_source_file", "_source_index", "timestamp_iso", "timestamp_ist"}
FALSE_DEFAULT_FLAGS = {
    "is_geoblocked_for_viewer",
    "is_unsent_image_by_messenger_kid_parent",
}


def repair_string(value):
    """Reverse common UTF-8-as-CP1252/Latin-1 mojibake only when clearly improved."""
    if not isinstance(value, str) or not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value, False

    def score(text):
        return text.count("\ufffd") * 10 + sum(text.count(marker) for marker in MOJIBAKE_MARKERS)

    candidates = []
    for codec in ("cp1252", "latin-1"):
        try:
            candidate = value.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if candidate != value:
            candidates.append(candidate)

    if not candidates:
        return value, False
    best = min(candidates, key=score)
    # A successful recovery must remove at least one visible corruption marker.
    if score(best) < score(value):
        return best, True
    return value, False


def repair_nested(value, stats):
    if isinstance(value, str):
        result, changed = repair_string(value)
        stats["strings_repaired"] += int(changed)
        return result
    if isinstance(value, list):
        return [repair_nested(item, stats) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            new_key, changed = repair_string(key) if isinstance(key, str) else (key, False)
            stats["strings_repaired"] += int(changed)
            result[new_key] = repair_nested(item, stats)
        return result
    return value


def main():
    parser = argparse.ArgumentParser(description="Merge and normalize Instagram export JSON chunks.")
    parser.add_argument("--input-dir", type=Path, default=Path.cwd(),
                        help="Folder containing message_1.json etc. (default: current directory)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output path (default: INPUT_DIR/Instagram.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and report counts without writing output")
    args = parser.parse_args()

    folder = args.input_dir.expanduser().resolve()
    output_path = args.output.expanduser().resolve() if args.output else folder / "Instagram.json"
    sources = sorted(
        p for p in folder.glob("message_*.json")
        if re.fullmatch(r"message_\d+\.json", p.name) and p.resolve() != output_path
    )
    if not sources:
        print(f"ERROR: no message_*.json files found in {folder}", file=sys.stderr)
        return 2

    records = []
    stats = {
        "files_read": 0,
        "records_retained": 0,
        "strings_repaired": 0,
        "timestamps_normalized": 0,
        "invalid_or_missing_timestamps": 0,
    }
    warnings = []
    root_metadata = None

    for source in sources:
        try:
            with source.open("r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            warnings.append(f"{source.name}: {exc}")
            continue

        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            warnings.append(f"{source.name}: expected an object containing a messages list")
            continue

        stats["files_read"] += 1
        if root_metadata is None:
            root_metadata = {
                key: repair_nested(value, stats)
                for key, value in payload.items()
                if key != "messages"
            }

        for index, original in enumerate(payload["messages"]):
            if not isinstance(original, dict):
                warnings.append(f"{source.name}: record {index} is not an object; retained in _raw_record")
                records.append({"_raw_record": repair_nested(original, stats)})
                stats["records_retained"] += 1
                continue

            fixed = repair_nested(original, stats)
            record = {}
            for key, value in fixed.items():
                if key in INTERNAL_FIELDS:
                    continue
                if key in FALSE_DEFAULT_FLAGS and value is False:
                    continue
                # Keep core fields, all meaningful event/media fields, and any
                # non-empty metadata. Empty optional values add noise.
                if key in {"sender_name", "timestamp_ms", "content"} or value not in (None, "", [], {}):
                    record[key] = value

            stamp = record.get("timestamp_ms")
            if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
                try:
                    instant = datetime.fromtimestamp(stamp / 1000, timezone.utc).astimezone(IST)
                    record["timestamp_ist"] = instant.isoformat(timespec="milliseconds")
                    stats["timestamps_normalized"] += 1
                except (OverflowError, OSError, ValueError):
                    stats["invalid_or_missing_timestamps"] += 1
            else:
                stats["invalid_or_missing_timestamps"] += 1

            records.append(record)
            stats["records_retained"] += 1

    if not stats["files_read"]:
        print("ERROR: no valid source files could be read.", file=sys.stderr)
        for warning in warnings:
            print(f"  - {warning}", file=sys.stderr)
        return 2

    def chronological_key(record):
        stamp = record.get("timestamp_ms")
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            return (0, stamp)
        return (1, 0)

    records.sort(key=chronological_key)

    print(f"Source files read: {stats['files_read']}")
    print(f"Records retained: {stats['records_retained']:,}")
    print(f"Strings repaired: {stats['strings_repaired']:,}")
    print(f"Timestamps converted to IST: {stats['timestamps_normalized']:,}")
    print(f"Missing/invalid timestamps: {stats['invalid_or_missing_timestamps']:,}")
    print(f"Output: {output_path}")
    for warning in warnings:
        print(f"WARNING: {warning}")

    if args.dry_run:
        print("Dry run only; no output written.")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("{")
            if root_metadata:
                for key, value in root_metadata.items():
                    handle.write("\n  " + json.dumps(key, ensure_ascii=False) + ": ")
                    handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + ",")
            handle.write("\n  \"messages\": [")
            if records:
                handle.write("\n")
                for index, record in enumerate(records):
                    if index:
                        handle.write(",\n")
                    handle.write("    " + json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n  ")
            handle.write("]\n}\n")
        temp_path.replace(output_path)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"ERROR writing output: {exc}", file=sys.stderr)
        return 2

    # Parse the generated file before declaring success.
    try:
        with output_path.open("r", encoding="utf-8") as handle:
            check = json.load(handle)
        if not isinstance(check.get("messages"), list) or len(check["messages"]) != len(records):
            raise ValueError("output validation failed: record count mismatch")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: output was written but validation failed: {exc}", file=sys.stderr)
        return 2

    print("Done. Output JSON validated; source files were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
