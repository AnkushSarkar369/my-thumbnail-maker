#!/usr/bin/env python3
"""
Reality.py — factual statistics from a unified chat and call archive.

Expected input files in the same directory as this script:
    I_Simple.json
    W_Old.json
    W_New.json
    calls.xml

Run:
    python Reality.py

The source archives are read-only. Records are never deduplicated or rewritten.
Only Python's standard library is required.
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
DISPLAY_TIMEZONE = ZoneInfo("Asia/Kolkata")
JSON_FILES = {
    "Instagram": BASE_DIR / "I_Simple.json",
    "WhatsApp (old)": BASE_DIR / "W_Old.json",
    "WhatsApp (new)": BASE_DIR / "W_New.json",
}
CALLS_FILE = BASE_DIR / "calls.xml"

CANONICAL_NAMES = {
    "ankush": "Ankush Sarkar",
    "ankush sarkar": "Ankush Sarkar",
    "gargee": "Gargee Basak",
    "gargee basak": "Gargee Basak",
}

# Matches common emoji code points. Joined emoji may contain more than one
# matched code point, so count matched emoji sequences rather than code points.
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002300-\U000023FF"
    "\U0001F1E6-\U0001F1FF"
    "\U0000FE0F"
    "\U0000200D"
    "\U0001F3FB-\U0001F3FF"
    "]+",
    flags=re.UNICODE,
)
WORD_PATTERN = re.compile(r"\b[\w]+(?:['’][\w]+)*\b", flags=re.UNICODE)

# Only calls matching Gargee's contact name or an explicitly configured phone
# number are included. Add any alternate saved contact names here if needed.
CALL_CONTACT_ALIASES = {"gargee", "gargee basak", "pendulum"}
# Optional: digits-only phone numbers for Gargee, including country code.
# Leave empty if the XML reliably provides her contact_name.
CALL_PHONE_NUMBERS: set[str] = set()

KNOWN_CALL_TYPES = {"1", "2", "3", "4", "5", "6", "7"}


def canonical_name(name: object) -> str:
    value = str(name or "Unknown").strip()
    return CANONICAL_NAMES.get(value.casefold(), value)


def parse_timestamp(value: object) -> datetime | None:
    """Parse epoch seconds/milliseconds or ISO-like datetime strings."""
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        # Millisecond epoch timestamps are around 13 digits; seconds around 10.
        if abs(number) >= 100_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    raw = str(value).strip()
    if not raw:
        return None

    # Numeric epochs may also be stored as strings.
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

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def chronological_key(dt: datetime) -> float:
    """
    Use absolute instants for aware timestamps. Naive timestamps have no
    known timezone; UTC is used only as a deterministic sorting fallback.
    """
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=timezone.utc).timestamp()
    return dt.timestamp()


def display_timestamp(dt: datetime) -> str:
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.strftime("%d %b %Y, %H:%M:%S") + " (timezone unknown)"
    return dt.astimezone(DISPLAY_TIMEZONE).strftime("%d %b %Y, %H:%M:%S IST")


def display_timestamp_plain(dt: datetime) -> str:
    """Format in IST without repeating the zone on compact range lines."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.strftime("%d %b %Y, %H:%M:%S")
    return dt.astimezone(DISPLAY_TIMEZONE).strftime("%d %b %Y, %H:%M:%S")


def display_date(dt: datetime) -> str:
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.strftime("%d %b %Y")
    return dt.astimezone(DISPLAY_TIMEZONE).strftime("%d %b %Y")


def fmt_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
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


def read_json_events(path: Path, source: str, warnings: list[str]) -> list[dict]:
    events = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        warnings.append(f"Missing input file: {path.name}")
        return events
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"Could not read {path.name}: {exc}")
        return events

    if isinstance(data, dict):
        data = data.get("messages", data.get("events", []))
    if not isinstance(data, list):
        warnings.append(f"{path.name}: expected a list of records (or a messages/events list).")
        return events

    for index, record in enumerate(data, start=1):
        if not isinstance(record, dict):
            warnings.append(f"{path.name} record {index}: record is not an object.")
            continue

        timestamp = parse_timestamp(record.get("timestamp"))
        if timestamp is None:
            warnings.append(f"{path.name} record {index}: missing or invalid timestamp.")
            continue

        sender_raw = record.get("sender", "Unknown")
        sender = canonical_name(sender_raw)
        event_type = str(record.get("type") or "unknown").strip() or "unknown"
        message_value = record.get("message", "")
        message = message_value if isinstance(message_value, str) else str(message_value or "")
        is_system = str(sender_raw or "").strip().casefold() in {
            "system", "whatsapp", "instagram", "meta"
        } or sender.casefold() == "system"

        events.append({
            "timestamp": timestamp,
            "sender": sender,
            "sender_raw": str(sender_raw or "Unknown").strip(),
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


def read_call_events(path: Path, warnings: list[str]) -> tuple[list[dict], int]:
    events = []
    excluded = 0
    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError:
        warnings.append(f"Missing input file: {path.name}")
        return events, excluded
    except (ET.ParseError, OSError) as exc:
        warnings.append(f"Could not read {path.name}: {exc}")
        return events, excluded

    for index, call in enumerate(root.findall("call"), start=1):
        contact = (call.attrib.get("contact_name") or "").strip()
        number = (call.attrib.get("number") or "").strip()
        contact_key = normalize_contact(contact)
        phone_key = normalize_phone(number)
        target_contact = (
            contact_key in {normalize_contact(alias) for alias in CALL_CONTACT_ALIASES}
            or (phone_key and phone_key in {normalize_phone(item) for item in CALL_PHONE_NUMBERS})
        )
        if not target_contact:
            excluded += 1
            continue

        try:
            raw_date = call.attrib.get("date")
            if raw_date is None:
                raise ValueError("missing date")
            timestamp = parse_timestamp(int(raw_date) / 1000)
            if timestamp is None:
                raise ValueError("invalid date")
        except (ValueError, TypeError, OverflowError):
            warnings.append(f"{path.name} call record {index}: missing or invalid date.")
            continue

        raw_type = str(call.attrib.get("type", "unknown"))
        duration_raw = call.attrib.get("duration", "0")
        try:
            duration = max(0, int(duration_raw))
        except (ValueError, TypeError):
            warnings.append(f"{path.name} call record {index}: invalid duration {duration_raw!r}.")
            duration = 0

        name = "Gargee Basak"
        if raw_type not in KNOWN_CALL_TYPES:
            warnings.append(f"{path.name} call record {index}: unknown call type code {raw_type!r}.")

        events.append({
            "timestamp": timestamp,
            "sender": name,
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
    return events, excluded


def exact_duplicate_stats(events: list[dict]) -> tuple[int, int]:
    """Return (number of duplicate groups, extra occurrences) across JSON records."""
    counts = Counter()
    for event in events:
        if event["source"] == "Calls":
            continue
        raw = event["raw_record"]
        # All normalized fields are considered, including any additional fields.
        fingerprint = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        counts[fingerprint] += 1
    groups = sum(1 for count in counts.values() if count > 1)
    extras = sum(count - 1 for count in counts.values() if count > 1)
    return groups, extras


def main() -> int:
    warnings: list[str] = []
    events: list[dict] = []
    json_events: list[dict] = []

    for source, path in JSON_FILES.items():
        loaded = read_json_events(path, source, warnings)
        events.extend(loaded)
        json_events.extend(loaded)

    calls_path = CALLS_FILE if CALLS_FILE.exists() else None
    if calls_path is None:
        xml_candidates = sorted(BASE_DIR.glob("*.xml"))
        if len(xml_candidates) == 1:
            calls_path = xml_candidates[0]
        elif len(xml_candidates) > 1:
            warnings.append(
                "calls.xml was not found and multiple XML files exist; "
                "rename the consolidated call archive to calls.xml."
            )
        else:
            warnings.append("Missing calls.xml (no XML file found in the script directory).")

    call_events, excluded_call_records = (
        read_call_events(calls_path, warnings) if calls_path else ([], 0)
    )
    events.extend(call_events)

    if not events:
        print("REALITY.PY — no valid timestamped records were loaded.")
        print("\nCheck that I_Simple.json, W_Old.json, W_New.json, and calls.xml are")
        print("in the same directory as Reality.py.")
        for warning in warnings:
            print(f"Reality.py: {warning}", file=sys.stderr)
        return 1

    events.sort(key=lambda event: chronological_key(event["timestamp"]))

    text_events = [
        event for event in json_events
        if event["type"].casefold() == "text"
        and not event["is_system"]
        and event["message"].strip()
    ]
    system_events = [event for event in json_events if event["is_system"]]
    empty_text_entries = [
        event for event in json_events
        if event["type"].casefold() == "text"
        and not event["is_system"]
        and not event["message"].strip()
    ]
    other_events = [
        event for event in json_events
        if event["type"].casefold() != "text" and not event["is_system"]
    ]

    participants = sorted({event["sender"] for event in text_events})
    participant_stats = {
        participant: {
            "messages": 0,
            "words": 0,
            "characters": 0,
            "emojis": 0,
            "by_source": Counter(),
        }
        for participant in participants
    }

    for event in text_events:
        stats = participant_stats[event["sender"]]
        message = event["message"]
        stats["messages"] += 1
        stats["words"] += len(WORD_PATTERN.findall(message))
        stats["characters"] += len(message)
        stats["emojis"] += len(EMOJI_PATTERN.findall(message))
        stats["by_source"][event["source"]] += 1

    event_type_counts = Counter(event["type"] for event in other_events)
    duplicate_groups, duplicate_extras = exact_duplicate_stats(json_events)

    total_recorded_duration = sum(event["duration"] for event in call_events)
    longest_call = max(call_events, key=lambda event: event["duration"], default=None)
    first_call = min(call_events, key=lambda event: chronological_key(event["timestamp"]), default=None)
    last_call = max(call_events, key=lambda event: chronological_key(event["timestamp"]), default=None)

    # The chronology combines timestamped events from all three chat archives
    # and the call log. Gaps are between adjacent recorded events, not claims
    # that no communication occurred elsewhere.
    gaps = []
    for previous, following in zip(events, events[1:]):
        seconds = chronological_key(following["timestamp"]) - chronological_key(previous["timestamp"])
        if seconds >= 0:
            gaps.append((seconds, previous, following))
    top_gaps = sorted(gaps, key=lambda item: item[0], reverse=True)[:5]

    sep = "═" * 70
    line = "─" * 70
    first_event = events[0]
    last_event = events[-1]

    print(sep)
    print("                              REALITY")
    print("                    Unified Conversation Archive")
    print(
        f"        {display_date(first_event['timestamp'])} — "
        f"{display_date(last_event['timestamp'])} · All times IST"
    )
    print(sep)

    print("\nOVERVIEW")
    print(line)
    print(f"{'Text messages':<42}{len(text_events):>12,}")
    for participant in sorted(participant_stats):
        print(
            f"  {participant:<40}"
            f"{participant_stats[participant]['messages']:>12,}"
        )
    print(f"{'Other chat events':<42}{len(other_events):>12,}")
    print(f"{'Empty text entries':<42}{len(empty_text_entries):>12,}")
    print(f"{'System events':<42}{len(system_events):>12,}")
    print(f"{'Calls with Gargee':<42}{len(call_events):>12,}")
    print(line)
    print(f"{'Total recorded events':<42}{len(events):>12,}")
    print()
    print(f"{'First recorded event':<30}{display_timestamp(first_event['timestamp'])}")
    print(f"{'Last recorded event':<30}{display_timestamp(last_event['timestamp'])}")

    print("\nMESSAGE STATISTICS")
    print(line)
    participant_header = (
        f"{'Participant':<22} {'Messages':>10} {'Words':>12} "
        f"{'Characters':>13} {'Emojis':>9}"
    )
    print(participant_header)
    print("─" * len(participant_header))
    totals = {"messages": 0, "words": 0, "characters": 0, "emojis": 0}
    for participant in sorted(participant_stats):
        stats = participant_stats[participant]
        print(
            f"{participant[:22]:<22} {stats['messages']:>10,} "
            f"{stats['words']:>12,} {stats['characters']:>13,} "
            f"{stats['emojis']:>9,}"
        )
        for key in totals:
            totals[key] += stats[key]
    print("─" * len(participant_header))
    print(
        f"{'TOTAL':<22} {totals['messages']:>10,} "
        f"{totals['words']:>12,} {totals['characters']:>13,} "
        f"{totals['emojis']:>9,}"
    )

    print("\nMESSAGES BY PLATFORM")
    print(line)
    platform_header = (
        f"{'Participant':<22} {'Instagram':>12} {'WhatsApp Old':>15} "
        f"{'WhatsApp New':>15} {'Total':>10}"
    )
    print(platform_header)
    print("─" * len(platform_header))
    platform_totals = {source: 0 for source in JSON_FILES}
    for participant in sorted(participant_stats):
        by_source = participant_stats[participant]["by_source"]
        counts = [by_source[source] for source in JSON_FILES]
        for source, count in zip(JSON_FILES, counts):
            platform_totals[source] += count
        print(
            f"{participant[:22]:<22} {counts[0]:>12,} {counts[1]:>15,} "
            f"{counts[2]:>15,} {sum(counts):>10,}"
        )
    print("─" * len(platform_header))
    print(
        f"{'TOTAL':<22} {platform_totals['Instagram']:>12,} "
        f"{platform_totals['WhatsApp (old)']:>15,} "
        f"{platform_totals['WhatsApp (new)']:>15,} "
        f"{sum(platform_totals.values()):>10,}"
    )

    print("\nOTHER CHAT EVENTS")
    print(line)
    event_header = f"{'Event type':<44}{'Count':>12}"
    print(event_header)
    print("─" * len(event_header))
    for event_type, count in sorted(event_type_counts.items(), key=lambda item: (-item[1], item[0])):
        print(f"{event_type:<44}{count:>12,}")
    print("─" * len(event_header))
    print(f"{'TOTAL':<44}{len(other_events):>12,}")

    print("\nCALLS")
    print(line)
    print(f"{'Total calls':<42}{len(call_events):>12,}")
    print(f"{'Total call duration':<42}{fmt_duration(total_recorded_duration):>12}")
    if longest_call:
        print(f"{'Longest call':<42}{fmt_duration(longest_call['duration']):>12}")
        print(f"{'Date of longest call':<30}{display_timestamp(longest_call['timestamp'])}")
    if first_call and last_call:
        first_call_date = display_date(first_call["timestamp"])
        last_call_date = display_date(last_call["timestamp"])
        print(f"{'Recorded call date range':<30}{first_call_date} — {last_call_date}")

    print("\nLONGEST GAPS BETWEEN RECORDED EVENTS")
    print(line)
    if not top_gaps:
        print("Not enough timestamped events to calculate gaps.")
    else:
        for rank, (seconds, previous, following) in enumerate(top_gaps, start=1):
            start = display_timestamp_plain(previous["timestamp"])
            end = display_timestamp_plain(following["timestamp"])
            print(f"{rank}. {fmt_duration(seconds):<17} [{start} — {end} IST]")

    print("\nARCHIVE INTEGRITY")
    print(line)
    print(f"{'Exact duplicate groups':<42}{duplicate_groups:>12,}")
    print(f"{'Extra occurrences in duplicate groups':<42}{duplicate_extras:>12,}")
    print()
    print("Original archive files remain unchanged.")

    print(sep)
    if warnings:
        print("\nReality.py validation notes (stderr; not part of the report):", file=sys.stderr)
        for warning in warnings[:50]:
            print(f"- {warning}", file=sys.stderr)
        if len(warnings) > 50:
            print(f"- ... and {len(warnings) - 50:,} more note(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
