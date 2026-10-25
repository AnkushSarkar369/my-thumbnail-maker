#!/usr/bin/env python3
"""Reality.py — factual statistics from Instagram, WhatsApp, and call archives.

Expected files beside this script:
    Instagram.json
    W_Old.json
    W_New.json
    calls.xml

Uses only Python's standard library. Source archives are read-only.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")
JSON_FILES = {
    "Instagram": BASE_DIR / "Instagram.json",
    "WhatsApp (old)": BASE_DIR / "W_Old.json",
    "WhatsApp (new)": BASE_DIR / "W_New.json",
}
CALLS_FILE = BASE_DIR / "calls.xml"

CANONICAL_NAMES = {
    "ankush": "Ankush Sarkar",
    "ankush sarkar": "Ankush Sarkar",
    "gargee": "Gargee Basak",
    "gargee basak": "Gargee Basak",
    "꧁•༆༆•": "Gargee Basak",
}
CALL_CONTACT_ALIASES = {"gargee", "gargee basak", "pendulum"}
CALL_PHONE_NUMBERS: set[str] = set()
KNOWN_CALL_TYPES = {"1", "2", "3", "4", "5", "6", "7"}

WORD_PATTERN = re.compile(r"\b[\w]+(?:['’][\w]+)*\b", re.UNICODE)
# Counts clusters approximately rather than counting every code point in a
# joined emoji sequence as a separate emoji.
EMOJI_PATTERN = re.compile(
    r"(?:[\U0001F1E6-\U0001F1FF]{2}"
    r"|[\U0001F300-\U0001FAFF\u2600-\u27BF\u2300-\u23FF]"
    r"[\uFE0F\U0001F3FB-\U0001F3FF]*"
    r"(?:\u200D[\U0001F300-\U0001FAFF\u2600-\u27BF\u2300-\u23FF]"
    r"[\uFE0F\U0001F3FB-\U0001F3FF]*)*)",
    re.UNICODE,
)


def canonical_name(value: object) -> str:
    name = str(value or "Unknown").strip()
    return CANONICAL_NAMES.get(name.casefold(), name)


def parse_timestamp(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if abs(number) >= 100_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        try:
            return parse_timestamp(float(raw))
        except ValueError:
            return None
    iso = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def sort_time(dt: datetime) -> float:
    if dt.tzinfo is None or dt.utcoffset() is None:
        # Archives without timezone information cannot be placed with certainty.
        # Keep the old deterministic fallback for sorting and gap calculations.
        return dt.replace(tzinfo=timezone.utc).timestamp()
    return dt.timestamp()


def fmt_time(dt: datetime, include_zone: bool = True) -> str:
    if dt.tzinfo is None or dt.utcoffset() is None:
        suffix = " (timezone unknown)" if include_zone else ""
        return dt.strftime("%d %b %Y, %H:%M:%S") + suffix
    rendered = dt.astimezone(IST).strftime("%d %b %Y, %H:%M:%S")
    return rendered + (" IST" if include_zone else "")


def fmt_date(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d %b %Y") if dt.tzinfo else dt.strftime("%d %b %Y")


def fmt_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def get_record_timestamp(record: dict) -> datetime | None:
    # Prefer the numeric source timestamp where available; Instagram.json keeps
    # timestamp_ms, and its timestamp_ist field is a readable companion.
    for key in ("timestamp_ms", "timestamp", "timestamp_ist", "date", "datetime", "created_at"):
        if key in record:
            parsed = parse_timestamp(record[key])
            if parsed is not None:
                return parsed
    return None


def infer_event_type(record: dict, message: str, source: str) -> str:
    explicit = record.get("type") or record.get("event_type") or record.get("message_type")
    if explicit:
        return str(explicit).strip().casefold() or "unknown"
    if message.strip():
        return "text"
    # Instagram export metadata for records without text.
    for key, label in (
        ("reactions", "reaction"), ("share", "share"),
        ("photos", "photo"), ("videos", "video"),
        ("audio_files", "audio"), ("files", "file"),
        ("call_duration", "call"),
    ):
        value = record.get(key)
        if value not in (None, "", [], {}):
            return label
    return "unknown"


def read_json_events(path: Path, source: str, diagnostics: Counter) -> list[dict]:
    events = []
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        diagnostics[f"{source}: missing file"] += 1
        return events
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        diagnostics[f"{source}: unreadable file"] += 1
        print(f"Reality.py: could not read {path.name}: {exc}", file=sys.stderr)
        return events

    if isinstance(data, dict):
        data = data.get("messages", data.get("events", []))
    if not isinstance(data, list):
        diagnostics[f"{source}: invalid root structure"] += 1
        print(f"Reality.py: {path.name} must contain a list or a messages list.", file=sys.stderr)
        return events

    for index, record in enumerate(data, start=1):
        if not isinstance(record, dict):
            diagnostics[f"{source}: non-object records"] += 1
            continue

        timestamp = get_record_timestamp(record)
        if timestamp is None:
            diagnostics[f"{source}: invalid timestamps"] += 1
            continue

        # Instagram.json uses sender_name/content; older WhatsApp archives may
        # use sender/message. Support both without changing either archive.
        sender_raw = (
            record.get("sender_name") or record.get("sender")
            or record.get("author") or record.get("from") or "Unknown"
        )
        sender = canonical_name(sender_raw)
        message_value = record.get("content")
        if message_value is None:
            message_value = record.get("message", record.get("text", ""))
        message = message_value if isinstance(message_value, str) else str(message_value or "")
        event_type = infer_event_type(record, message, source)
        sender_key = str(sender_raw).strip().casefold()
        is_system = sender_key in {"system", "whatsapp", "instagram", "meta"} or sender.casefold() == "system"

        events.append({
            "timestamp": timestamp,
            "sender": sender,
            "sender_raw": str(sender_raw).strip(),
            "type": event_type,
            "message": message,
            "source": source,
            "record_index": index,
            "is_system": is_system,
            "raw_record": record,
        })
    return events


def normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def normalize_contact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def read_call_events(path: Path, diagnostics: Counter) -> list[dict]:
    events = []
    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError:
        diagnostics["Calls: missing file"] += 1
        return events
    except (ET.ParseError, OSError) as exc:
        diagnostics["Calls: unreadable file"] += 1
        print(f"Reality.py: could not read {path.name}: {exc}", file=sys.stderr)
        return events

    aliases = {normalize_contact(name) for name in CALL_CONTACT_ALIASES}
    numbers = {normalize_phone(number) for number in CALL_PHONE_NUMBERS}
    for index, call in enumerate(root.findall("call"), start=1):
        contact = (call.attrib.get("contact_name") or "").strip()
        number = (call.attrib.get("number") or "").strip()
        if normalize_contact(contact) not in aliases and normalize_phone(number) not in numbers:
            continue

        try:
            raw_date = call.attrib.get("date")
            if raw_date is None:
                raise ValueError
            timestamp = parse_timestamp(int(raw_date))
            if timestamp is None:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            diagnostics["Calls: invalid timestamps"] += 1
            continue

        try:
            duration = max(0, int(call.attrib.get("duration", "0")))
        except (ValueError, TypeError):
            diagnostics["Calls: invalid durations"] += 1
            duration = 0

        raw_type = str(call.attrib.get("type", "unknown"))
        if raw_type not in KNOWN_CALL_TYPES:
            diagnostics["Calls: unknown type codes"] += 1
        events.append({
            "timestamp": timestamp,
            "sender": "Gargee Basak",
            "sender_raw": contact or number or "Unknown contact",
            "type": "call",
            "message": "",
            "source": "Calls",
            "record_index": index,
            "is_system": False,
            "call_type_code": raw_type,
            "duration": duration,
            "number": number,
        })
    return events


def duplicate_stats(events: list[dict]) -> tuple[int, int]:
    counts = Counter()
    for event in events:
        if event["source"] == "Calls":
            continue
        fingerprint = json.dumps(
            event["raw_record"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        counts[fingerprint] += 1
    return (
        sum(count > 1 for count in counts.values()),
        sum(count - 1 for count in counts.values() if count > 1),
    )


def main() -> int:
    diagnostics = Counter()
    chat_events = []
    for source, path in JSON_FILES.items():
        chat_events.extend(read_json_events(path, source, diagnostics))

    call_events = read_call_events(CALLS_FILE, diagnostics)
    events = chat_events + call_events
    if not events:
        print("REALITY — no valid timestamped records were loaded.")
        print("Expected Instagram.json, W_Old.json, W_New.json, and calls.xml beside Reality.py.")
        for key, count in sorted(diagnostics.items()):
            print(f"Reality.py: {key}: {count}", file=sys.stderr)
        return 1

    events.sort(key=lambda event: sort_time(event["timestamp"]))
    text_events = [
        event for event in chat_events
        if event["type"] == "text" and not event["is_system"] and event["message"].strip()
    ]
    system_events = [event for event in chat_events if event["is_system"]]
    empty_text_entries = [
        event for event in chat_events
        if event["type"] == "text" and not event["is_system"] and not event["message"].strip()
    ]
    other_events = [
        event for event in chat_events
        if event["type"] != "text" and not event["is_system"]
    ]

    participant_stats = {}
    for event in text_events:
        stats = participant_stats.setdefault(event["sender"], {
            "messages": 0, "words": 0, "characters": 0, "emojis": 0, "by_source": Counter()
        })
        stats["messages"] += 1
        stats["words"] += len(WORD_PATTERN.findall(event["message"]))
        stats["characters"] += len(event["message"])
        stats["emojis"] += len(EMOJI_PATTERN.findall(event["message"]))
        stats["by_source"][event["source"]] += 1

    event_type_counts = Counter(event["type"] for event in other_events)
    duplicate_groups, duplicate_extras = duplicate_stats(chat_events)
    total_call_duration = sum(event["duration"] for event in call_events)
    longest_call = max(call_events, key=lambda event: event["duration"], default=None)
    first_call = min(call_events, key=lambda event: sort_time(event["timestamp"]), default=None)
    last_call = max(call_events, key=lambda event: sort_time(event["timestamp"]), default=None)

    gaps = []
    for previous, following in zip(events, events[1:]):
        seconds = sort_time(following["timestamp"]) - sort_time(previous["timestamp"])
        if seconds >= 0:
            gaps.append((seconds, previous, following))
    top_gaps = sorted(gaps, key=lambda item: item[0], reverse=True)[:5]

    separator = "═" * 70
    rule = "─" * 70
    first_event, last_event = events[0], events[-1]
    print(separator)
    print("                              REALITY")
    print("                    Unified Conversation Archive")
    print(f"        {fmt_date(first_event['timestamp'])} — {fmt_date(last_event['timestamp'])} · All times IST")
    print(separator)

    print("\nOVERVIEW")
    print(rule)
    print(f"{'Text messages':<42}{len(text_events):>12,}")
    for participant in sorted(participant_stats):
        print(f"  {participant:<40}{participant_stats[participant]['messages']:>12,}")
    print(f"{'Other chat events':<42}{len(other_events):>12,}")
    print(f"{'Empty text entries':<42}{len(empty_text_entries):>12,}")
    print(f"{'System events':<42}{len(system_events):>12,}")
    print(f"{'Calls with Gargee':<42}{len(call_events):>12,}")
    print(rule)
    print(f"{'Total recorded events':<42}{len(events):>12,}")
    print(f"\n{'First recorded event':<30}{fmt_time(first_event['timestamp'])}")
    print(f"{'Last recorded event':<30}{fmt_time(last_event['timestamp'])}")

    print("\nMESSAGE STATISTICS")
    print(rule)
    header = f"{'Participant':<22} {'Messages':>10} {'Words':>12} {'Characters':>13} {'Emojis':>9}"
    print(header)
    print("─" * len(header))
    totals = Counter()
    for participant in sorted(participant_stats):
        stats = participant_stats[participant]
        print(f"{participant[:22]:<22} {stats['messages']:>10,} {stats['words']:>12,} {stats['characters']:>13,} {stats['emojis']:>9,}")
        for key in ("messages", "words", "characters", "emojis"):
            totals[key] += stats[key]
    print("─" * len(header))
    print(f"{'TOTAL':<22} {totals['messages']:>10,} {totals['words']:>12,} {totals['characters']:>13,} {totals['emojis']:>9,}")

    print("\nMESSAGES BY PLATFORM")
    print(rule)
    header = f"{'Participant':<22} {'Instagram':>12} {'WhatsApp Old':>15} {'WhatsApp New':>15} {'Total':>10}"
    print(header)
    print("─" * len(header))
    platform_totals = Counter()
    for participant in sorted(participant_stats):
        counts = [participant_stats[participant]["by_source"][name] for name in JSON_FILES]
        for name, count in zip(JSON_FILES, counts):
            platform_totals[name] += count
        print(f"{participant[:22]:<22} {counts[0]:>12,} {counts[1]:>15,} {counts[2]:>15,} {sum(counts):>10,}")
    print("─" * len(header))
    print(f"{'TOTAL':<22} {platform_totals['Instagram']:>12,} {platform_totals['WhatsApp (old)']:>15,} {platform_totals['WhatsApp (new)']:>15,} {sum(platform_totals.values()):>10,}")

    print("\nOTHER CHAT EVENTS")
    print(rule)
    header = f"{'Event type':<44}{'Count':>12}"
    print(header)
    print("─" * len(header))
    for event_type, count in sorted(event_type_counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"{event_type:<44}{count:>12,}")
    print("─" * len(header))
    print(f"{'TOTAL':<44}{len(other_events):>12,}")

    print("\nCALLS")
    print(rule)
    print(f"{'Total calls':<42}{len(call_events):>12,}")
    print(f"{'Total call duration':<42}{fmt_duration(total_call_duration):>12}")
    if longest_call:
        print(f"{'Longest call':<42}{fmt_duration(longest_call['duration']):>12}")
        print(f"{'Date of longest call':<30}{fmt_time(longest_call['timestamp'])}")
    if first_call and last_call:
        print(f"{'Recorded call date range':<30}{fmt_date(first_call['timestamp'])} — {fmt_date(last_call['timestamp'])}")

    print("\nLONGEST GAPS BETWEEN RECORDED EVENTS")
    print(rule)
    if not top_gaps:
        print("Not enough timestamped events to calculate gaps.")
    else:
        for rank, (seconds, previous, following) in enumerate(top_gaps, start=1):
            print(f"{rank}. {fmt_duration(seconds):<17} [{fmt_time(previous['timestamp'], False)} — {fmt_time(following['timestamp'], False)} IST]")

    print("\nARCHIVE INTEGRITY")
    print(rule)
    print(f"{'Exact duplicate groups':<42}{duplicate_groups:>12,}")
    print(f"{'Extra occurrences in duplicate groups':<42}{duplicate_extras:>12,}")
    print("\nOriginal archive files remain unchanged.")
    print(separator)

    if diagnostics:
        print("\nReality.py validation notes (stderr; not part of the report):", file=sys.stderr)
        for key, count in sorted(diagnostics.items()):
            print(f"- {key}: {count}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
