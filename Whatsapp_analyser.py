"""
WhatsApp Chat Analyzer (Final Enhanced)
Usage: python chat_analyzer.py <path_to_chat.txt>
"""

import re
import sys
from collections import Counter
from datetime import datetime, timedelta

MSG_PATTERN = re.compile(
    r"^(\d{2}/\d{2}/\d{4}), (\d{1,2}:\d{2}(?:\s?[ap]m)?) - ([^:]+): (.+)$",
    re.IGNORECASE
)

EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001FA00-\U0001FA9F"
    "\U00002300-\U000023FF"
    "\U0000FE00-\U0000FE0F"
    "\U0001F1E0-\U0001F1FF"
    "\u200d"
    "]+",
    flags=re.UNICODE
)

MEDIA_STRINGS = [
    "<media omitted>", "image omitted", "video omitted",
    "audio omitted", "sticker omitted", "document omitted",
    "gif omitted", "contact card omitted",
]

DAYS_OF_WEEK = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]


# ------------------ PARSER ------------------

def parse_chat(filepath):
    messages = []
    current = None

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            stripped = line.strip()
            if not stripped:
                continue

            m = MSG_PATTERN.match(stripped)
            if m:
                if current:
                    messages.append(current)
                date_str, time_str, sender, text = m.groups()
                time_str_clean = time_str.strip()
                for fmt in ("%d/%m/%Y %I:%M %p", "%d/%m/%Y %I:%M%p", "%d/%m/%Y %H:%M"):
                    try:
                        dt = datetime.strptime(f"{date_str} {time_str_clean}", fmt)
                        break
                    except ValueError:
                        continue
                else:
                    continue
                current = {"dt": dt, "sender": sender.strip(), "text": text.strip()}
            else:
                # continuation line of previous message
                if current:
                    current["text"] += "\n" + stripped

    if current:
        messages.append(current)

    return messages


# ------------------ HELPERS ------------------

def is_media(text):
    tl = text.lower().strip()
    return any(tl == s or (tl.startswith("<") and "omitted" in tl) for s in MEDIA_STRINGS)


def extract_emojis(text):
    return EMOJI_PATTERN.findall(text)


def tokenize(text):
    no_emoji = EMOJI_PATTERN.sub(" ", text)
    tokens = no_emoji.split()
    return [t for t in tokens if t]


def is_single_unit(text):
    stripped = text.strip()
    if EMOJI_PATTERN.fullmatch(stripped):
        return True
    no_emoji = EMOJI_PATTERN.sub("", stripped).strip()
    return len(no_emoji) == 1


# ------------------ ANALYSIS ------------------

def analyze(messages):
    senders = list(dict.fromkeys(m["sender"] for m in messages))

    stats = {s: {
        "messages": 0,
        "characters": 0,
        "tokens": [],
        "unique_tokens": set(),
        "emojis": [],
        "media_count": 0,
        "question_marks": 0,
        "single_unit_msgs": 0,
        "longest_msg_len": 0,
        "longest_msg_text": "",
    } for s in senders}

    daily_counts = Counter()
    sender_daily = {s: Counter() for s in senders}
    dow_counts = Counter()
    all_datetimes = [m["dt"] for m in messages]

    for msg in messages:
        s = msg["sender"]
        text = msg["text"]
        date = msg["dt"].date()
        dow = msg["dt"].weekday()

        daily_counts[date] += 1
        sender_daily[s][date] += 1
        dow_counts[dow] += 1

        if is_media(text):
            stats[s]["media_count"] += 1
            continue

        stats[s]["messages"] += 1
        stats[s]["characters"] += len(text)
        stats[s]["question_marks"] += text.count("?")

        if is_single_unit(text):
            stats[s]["single_unit_msgs"] += 1

        tokens = tokenize(text)
        stats[s]["tokens"].extend(tokens)
        stats[s]["unique_tokens"].update(t.lower() for t in tokens)

        emojis = extract_emojis(text)
        stats[s]["emojis"].extend(emojis)

        if len(text) > stats[s]["longest_msg_len"]:
            stats[s]["longest_msg_len"] = len(text)
            stats[s]["longest_msg_text"] = text

    response_times = {s: [] for s in senders}
    for i in range(1, len(messages)):
        prev = messages[i - 1]
        curr = messages[i]
        if prev["sender"] != curr["sender"]:
            delta = (curr["dt"] - prev["dt"]).total_seconds()
            response_times[curr["sender"]].append(delta)

    return stats, senders, daily_counts, sender_daily, dow_counts, all_datetimes, response_times


# ------------------ REPORT ------------------

def compute_gaps(all_datetimes):
    sorted_dts = sorted(all_datetimes)
    gaps = []
    for i in range(1, len(sorted_dts)):
        delta = sorted_dts[i] - sorted_dts[i - 1]
        gaps.append((delta, sorted_dts[i - 1], sorted_dts[i]))
    return sorted(gaps, reverse=True)


def compute_sessions(messages, gap_threshold_minutes=60):
    if not messages:
        return []

    threshold = timedelta(minutes=gap_threshold_minutes)
    sessions = []
    block = [messages[0]]

    for msg in messages[1:]:
        if msg["dt"] - block[-1]["dt"] > threshold:
            sessions.append(block)
            block = []
        block.append(msg)
    sessions.append(block)

    result = []
    for block in sessions:
        start = block[0]["dt"]
        end = block[-1]["dt"]
        duration = end - start
        sender_counts = Counter(m["sender"] for m in block)
        result.append({
            "start": start,
            "end": end,
            "duration": duration,
            "msg_count": len(block),
            "sender_counts": sender_counts,
        })

    return result


def fmt_duration(td):
    total_s = int(td.total_seconds())
    h = total_s // 3600
    m = (total_s % 3600) // 60
    return f"{h}h {m}m"


def print_report(stats, senders, daily_counts, sender_daily, dow_counts, all_datetimes, response_times, total_messages, messages):
    sep = "═" * 60

    print(sep)
    print("  WHATSAPP CHAT AUTOPSY REPORT")
    print(sep)

    total_days = len(daily_counts)
    avg_per_day = total_messages / total_days if total_days else 0

    print(f"\nOVERALL")
    print(f"  Total messages         : {total_messages:,}")
    print(f"  Total days covered     : {total_days:,}")
    print(f"  Avg messages/day       : {avg_per_day:.1f}")
    date_range = f"{min(daily_counts.keys())} → {max(daily_counts.keys())}"
    print(f"  Date range             : {date_range}")

    busiest_day, busiest_count = daily_counts.most_common(1)[0]
    print(f"  Busiest single day     : {busiest_day} ({busiest_count:,} messages)")

    gaps = compute_gaps(all_datetimes)
    if gaps:
        print(f"\n  Top 3 longest silences:")
        for i, (gap, start, end) in enumerate(gaps[:3], 1):
            hours = int(gap.total_seconds() // 3600)
            mins = int((gap.total_seconds() % 3600) // 60)
            print(f"    {i}. {hours}h {mins}m  ({start.strftime('%d %b %Y %I:%M %p')} → {end.strftime('%d %b %Y %I:%M %p')})")

    # ------------------ SESSION METRICS ------------------
    sessions = compute_sessions(messages, gap_threshold_minutes=60)

    total_time = sum((s["duration"] for s in sessions), timedelta())
    durations = [s["duration"] for s in sessions]
    avg_duration = sum(durations, timedelta()) / len(durations) if durations else timedelta()
    longest_session = max(sessions, key=lambda s: s["duration"]) if sessions else None
    busiest_session = max(sessions, key=lambda s: s["msg_count"]) if sessions else None

    sender_time = {s: timedelta() for s in senders}
    for sess in sessions:
        total_msgs_in_sess = sess["msg_count"]
        for s in senders:
            share = sess["sender_counts"].get(s, 0) / total_msgs_in_sess
            sender_time[s] += sess["duration"] * share

    print(f"\nSESSION METRICS  (gap threshold: 60 min)")
    print(f"  Total sessions         : {len(sessions)}")
    print(f"  Total time             : {fmt_duration(total_time)}")
    print(f"  Avg session length     : {fmt_duration(avg_duration)}")
    print(f"  Avg msgs/session       : {sum(s['msg_count'] for s in sessions)/len(sessions):.1f}")

    if longest_session:
        print(f"  Longest session        : {fmt_duration(longest_session['duration'])}  ({longest_session['start'].strftime('%d %b %Y')})")

    if busiest_session:
        print(f"  Most msgs in session   : {busiest_session['msg_count']}  ({busiest_session['start'].strftime('%d %b %Y')})")

    print(f"\n  Time share per person (weighted by message volume):")
    for s in senders:
        print(f"    {s:<25} {fmt_duration(sender_time[s])}")

    print(f"\n  Top 3 longest sessions:")
    top3 = sorted(sessions, key=lambda s: s["duration"], reverse=True)[:3]
    for i, sess in enumerate(top3, 1):
        s_fmt = sess["start"].strftime("%d %b %Y %I:%M %p")
        e_fmt = sess["end"].strftime("%I:%M %p")
        print(f"    {i}. {fmt_duration(sess['duration'])}  |  {sess['msg_count']} msgs  |  {s_fmt} → {e_fmt}")

    # ------------------ REST OF REPORT ------------------
    print(f"\nMESSAGES BY DAY OF WEEK")
    max_dow = max(dow_counts.values()) if dow_counts else 1
    for i, day in enumerate(DAYS_OF_WEEK):
        count = dow_counts.get(i, 0)
        bar = "█" * int(count / max_dow * 30)
        print(f"  {day:<10}  {count:>5,}  {bar}")

    print(f"\nPER PERSON")
    for s in senders:
        st = stats[s]

        total_words = len(st["tokens"])
        unique_words = len(st["unique_tokens"])
        avg_words = total_words / st["messages"] if st["messages"] else 0

        active_days = len(sender_daily[s])
        avg_day = st["messages"] / active_days if active_days else 0

        avg_resp = (
            sum(response_times[s]) / len(response_times[s])
            if response_times[s] else 0
        )

        emoji_total = len(st["emojis"])
        top_emojis = Counter(st["emojis"]).most_common(5)
        top_emoji_str = "  ".join(f"{e}×{c}" for e, c in top_emojis)

        monthly_s = Counter()
        for date, count in sender_daily[s].items():
            monthly_s[date.strftime("%Y-%m")] += count
        best_month, best_month_count = monthly_s.most_common(1)[0] if monthly_s else ("N/A", 0)

        lmt = st["longest_msg_text"]
        preview = lmt[:100].replace("\n", " ")
        preview_str = f'"{preview}{"..." if len(lmt) > 100 else ""}"'

        print(f"\n  ── {s} ──")
        print(f"    Messages sent          : {st['messages']:,}")
        print(f"    Active days            : {active_days:,}")
        print(f"    Avg msgs / active day  : {avg_day:.1f}")
        print(f"    Avg response time      : {avg_resp/60:.1f} min")
        print(f"    Total characters       : {st['characters']:,}")
        print(f"    Total words            : {total_words:,}")
        print(f"    Unique words           : {unique_words:,}")
        print(f"    Avg words/message      : {avg_words:.1f}")
        print(f"    Questions asked (?)    : {st['question_marks']:,}")
        print(f"    Single char/emoji msgs : {st['single_unit_msgs']:,}")
        print(f"    Total emojis           : {emoji_total:,}")
        print(f"    Media/stickers sent    : {st['media_count']:,}")
        print(f"    Most active month      : {best_month} ({best_month_count:,} messages)")
        print(f"    Top 5 emojis           : {top_emoji_str if top_emoji_str else 'none'}")
        print(f"    Longest message        : {st['longest_msg_len']:,} chars")
        print(f"    Longest msg preview    : {preview_str}")

    print(f"\nTOP 20 RAW WORDS PER PERSON")
    for s in senders:
        counter = Counter(t.lower() for t in stats[s]["tokens"])
        top = counter.most_common(20)
        print(f"\n  ── {s} ──")
        for rank, (token, count) in enumerate(top, 1):
            print(f"    {rank:>2}. {token:<20} {count:,}")

    monthly = Counter()
    for date, count in daily_counts.items():
        monthly[date.strftime("%Y-%m")] += count

    print(f"\nMONTHLY MESSAGE VOLUME (total)")
    for month in sorted(monthly):
        bar = "█" * (monthly[month] // 50)
        print(f"  {month}  {monthly[month]:>5,}  {bar}")

    print(f"\n{sep}")
    print("  END OF REPORT")
    print(sep)


# ------------------ MAIN ------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python chat_analyzer.py <path_to_chat.txt>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"Parsing: {filepath}")

    messages = parse_chat(filepath)
    print(f"Parsed {len(messages):,} messages. Running analysis...\n")

    stats, senders, daily_counts, sender_daily, dow_counts, all_datetimes, response_times = analyze(messages)
    total_messages = sum(st["messages"] + st["media_count"] for st in stats.values())

    print_report(stats, senders, daily_counts, sender_daily, dow_counts, all_datetimes, response_times, total_messages, messages)


if __name__ == "__main__":
    main()
