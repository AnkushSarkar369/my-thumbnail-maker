#!/usr/bin/env python3

import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
import csv
import argparse


def fmt(seconds):
    """Convert seconds into a human-readable duration."""
    seconds = int(seconds)

    d, seconds = divmod(seconds, 86400)
    h, seconds = divmod(seconds, 3600)
    m, s = divmod(seconds, 60)

    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")

    return " ".join(parts)


parser = argparse.ArgumentParser(description="Analyze SMS Backup & Restore Call Logs")
parser.add_argument("xml", help="Path to call log XML")
args = parser.parse_args()

tree = ET.parse(args.xml)
root = tree.getroot()

stats = defaultdict(lambda: {
    "calls": 0,
    "incoming": 0,
    "outgoing": 0,
    "missed": 0,
    "rejected": 0,
    "blocked": 0,
    "voicemail": 0,
    "answered_externally": 0,
    "duration": 0,
    "longest": 0,
    "first": None,
    "last": None
})

overall_duration = 0
overall_calls = 0
longest_call = None
all_calls = []

for call in root.findall("call"):

    number = call.attrib.get("number", "Unknown")
    name = call.attrib.get("contact_name")

    if not name or name.strip() == "":
        name = "(Unknown)"

    duration = int(call.attrib.get("duration", 0))
    ctype = call.attrib.get("type", "")
    timestamp = int(call.attrib["date"]) / 1000

    s = stats[name]

    s["calls"] += 1
    s["duration"] += duration
    s["longest"] = max(s["longest"], duration)

    if s["first"] is None or timestamp < s["first"]:
        s["first"] = timestamp

    if s["last"] is None or timestamp > s["last"]:
        s["last"] = timestamp

    if ctype == "1":
        s["incoming"] += 1
    elif ctype == "2":
        s["outgoing"] += 1
    elif ctype == "3":
        s["missed"] += 1
    elif ctype == "4":
        s["voicemail"] += 1
    elif ctype == "5":
        s["rejected"] += 1
    elif ctype == "6":
        s["blocked"] += 1
    elif ctype == "7":
        s["answered_externally"] += 1

    overall_calls += 1
    overall_duration += duration

    all_calls.append({
        "name": name,
        "duration": duration,
        "date": timestamp
    })

    if longest_call is None or duration > longest_call["duration"]:
        longest_call = {
            "name": name,
            "duration": duration
        }

# -------------------------------
# Overall Statistics
# -------------------------------

print("=" * 90)
print("CALL LOG STATISTICS")
print("=" * 90)

print(f"Total calls      : {overall_calls}")
print(f"Unique contacts  : {len(stats)}")
print(f"Total talk time  : {fmt(overall_duration)}")

if longest_call:
    print(f"Longest call     : {fmt(longest_call['duration'])} ({longest_call['name']})")

# -------------------------------
# Top Talk Time
# -------------------------------

print("\n" + "=" * 90)
print("TOP CONTACTS BY TALK TIME")
print("=" * 90)

ranking = sorted(
    stats.items(),
    key=lambda x: x[1]["duration"],
    reverse=True
)

for i, (name, s) in enumerate(ranking[:20], start=1):

    avg = s["duration"] / s["calls"]

    print(
        f"{i:2}. "
        f"{name[:35]:35} "
        f"{fmt(s['duration']):>12}   "
        f"{s['calls']:4} calls   "
        f"avg {fmt(avg)}"
    )

# -------------------------------
# Top Call Count
# -------------------------------

print("\n" + "=" * 90)
print("TOP CONTACTS BY CALL COUNT")
print("=" * 90)

ranking = sorted(
    stats.items(),
    key=lambda x: x[1]["calls"],
    reverse=True
)

for i, (name, s) in enumerate(ranking[:20], start=1):

    print(
        f"{i:2}. "
        f"{name[:35]:35} "
        f"{s['calls']:4} calls   "
        f"{fmt(s['duration'])}"
    )

# -------------------------------
# NEW: Incoming Leaderboard
# -------------------------------

print("\n" + "=" * 90)
print("TOP CONTACTS BY INCOMING CALLS")
print("=" * 90)

ranking = sorted(
    stats.items(),
    key=lambda x: x[1]["incoming"],
    reverse=True
)

for i, (name, s) in enumerate(ranking[:20], start=1):

    print(
        f"{i:2}. "
        f"{name[:35]:35} "
        f"{s['incoming']:4} incoming   "
        f"{s['calls']:4} total"
    )

# -------------------------------
# NEW: Longest Individual Calls
# -------------------------------

print("\n" + "=" * 90)
print("TOP 10 LONGEST INDIVIDUAL CALLS")
print("=" * 90)

top_calls = sorted(
    all_calls,
    key=lambda x: x["duration"],
    reverse=True
)[:10]

for i, c in enumerate(top_calls, start=1):

    print(
        f"{i:2}. "
        f"{fmt(c['duration']):>12}   "
        f"{c['name']}"
    )

# -------------------------------
# CSV Export
# -------------------------------

with open("call_statistics.csv", "w", newline="", encoding="utf-8") as f:

    writer = csv.writer(f)

    writer.writerow([
        "Name",
        "Calls",
        "Incoming",
        "Outgoing",
        "Missed",
        "Rejected",
        "Blocked",
        "Voicemail",
        "Answered Externally",
        "Total Seconds",
        "Formatted Time",
        "Average Seconds",
        "Longest Call",
        "First Call",
        "Last Call"
    ])

    for name, s in sorted(stats.items()):

        avg = s["duration"] / s["calls"] if s["calls"] else 0

        writer.writerow([
            name,
            s["calls"],
            s["incoming"],
            s["outgoing"],
            s["missed"],
            s["rejected"],
            s["blocked"],
            s["voicemail"],
            s["answered_externally"],
            s["duration"],
            fmt(s["duration"]),
            round(avg, 1),
            fmt(s["longest"]),
            datetime.fromtimestamp(s["first"]),
            datetime.fromtimestamp(s["last"])
        ])

print("\nCSV written to call_statistics.csv")
