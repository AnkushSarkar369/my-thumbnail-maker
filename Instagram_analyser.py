"""
Instagram Chat Analyzer (Final Final)
Usage: python chat_analyzer.py <path_to_chat.json>
"""

import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from datetime import timedelta
IST = timezone(timedelta(hours=5, minutes=30))
# ------------------ CONSTANTS ------------------

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

DAYS_OF_WEEK = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

# ------------------ PARSER ------------------

def parse_chat(filepath):
    messages = []

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = data.get("messages", [])

    for msg in data:
        sender = msg.get("sender", "unknown")
        ts = msg.get("timestampUnix")
        if not ts:
            continue

        dt = datetime.fromtimestamp(ts, timezone.utc)
        msg_type = msg.get("type", "")
        text_content = msg.get("text", "").strip()

        if msg_type == "text":
            if not text_content:
                continue
            category = "text"
            text = text_content

        elif msg_type in ("media", "raven_media"):
            category = "media"
            text = ""

        elif msg_type == "action_log" and "Reacted" in text_content:
            category = "reaction"
            text = text_content

        elif msg_type in (
            "clip", "media_share", "reel_share",
            "link", "video_call_event", "profile"
        ):
            category = "other"
            text = msg.get("clipCaption") or msg.get("linkUrl") or text_content

        elif msg_type == "placeholder":
            continue

        else:
            category = "other"
            text = text_content

        messages.append({
            "dt": dt,
            "sender": sender,
            "text": text,
            "category": category
        })

    messages.sort(key=lambda x: x["dt"])
    return messages


# ------------------ HELPERS ------------------

def extract_emojis(text):
    return EMOJI_PATTERN.findall(text)

def tokenize(text):
    return [t for t in EMOJI_PATTERN.sub(" ", text).split() if t]

def is_single_unit(text):
    stripped = text.strip()
    if EMOJI_PATTERN.fullmatch(stripped):
        return True
    return len(EMOJI_PATTERN.sub("", stripped).strip()) == 1


# ------------------ ANALYSIS ------------------

def analyze(messages):
    senders = list(dict.fromkeys(m["sender"] for m in messages))

    stats = {s: {
        "text_messages": 0,
        "media_messages": 0,
        "reactions": 0,
        "other_messages": 0,
        "tokens": [],
        "unique_tokens": set(),
        "emojis": [],
        "question_marks": 0,
        "single_unit_msgs": 0,
        "longest_msg_len": 0,
        "longest_msg_text": "",
        "reaction_types": Counter(),
    } for s in senders}

    daily_counts = Counter()
    sender_daily = {s: Counter() for s in senders}
    dow_counts = Counter()
    all_datetimes = []

    for msg in messages:
        s = msg["sender"]
        cat = msg["category"]
        dt = msg["dt"]
        text = msg["text"]

        all_datetimes.append(dt)
        daily_counts[dt.date()] += 1
        sender_daily[s][dt.date()] += 1
        dow_counts[dt.weekday()] += 1

        if cat == "text":
            stats[s]["text_messages"] += 1
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

        elif cat == "media":
            stats[s]["media_messages"] += 1

        elif cat == "reaction":
            stats[s]["reactions"] += 1
            for e in extract_emojis(text):
                stats[s]["reaction_types"][e] += 1

        else:
            stats[s]["other_messages"] += 1

    response_times = {s: [] for s in senders}
    for i in range(1, len(messages)):
        prev, curr = messages[i - 1], messages[i]
        if prev["sender"] != curr["sender"]:
            if prev["category"] in ("text","media") and curr["category"] in ("text","media"):
                delta = (curr["dt"] - prev["dt"]).total_seconds()
                response_times[curr["sender"]].append(delta)

    return stats, senders, daily_counts, sender_daily, dow_counts, all_datetimes, response_times


# ------------------ REPORT ------------------

def compute_sessions(messages, gap_threshold_minutes=60):
    """
    Split messages into sessions by gap threshold.
    Returns list of session dicts:
      { start, end, duration, msg_count, sender_counts }
    """
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

def compute_gaps(all_datetimes):
    s = sorted(all_datetimes)
    gaps = []
    for i in range(1, len(s)):
        gaps.append((s[i]-s[i-1], s[i-1], s[i]))
    return sorted(gaps, reverse=True)


def fmt_duration(td):
    total_s = int(td.total_seconds())
    h = total_s // 3600
    m = (total_s % 3600) // 60
    return f"{h}h {m}m"


def print_report(stats, senders, daily_counts, sender_daily, dow_counts, all_datetimes, response_times, messages):
    sep = "═"*60

    print(sep)
    print("  INSTAGRAM CHAT AUTOPSY REPORT")
    print(sep)

    total = sum(
        st["text_messages"] + st["media_messages"] + st["reactions"] + st["other_messages"]
        for st in stats.values()
    )

    days = len(daily_counts)
    print(f"\nOVERALL")
    print(f"  Total events         : {total:,}")
    print(f"  Days covered         : {days}")
    print(f"  Avg/day              : {total/days:.1f}")
    print(f"  Date range           : {min(daily_counts)} → {max(daily_counts)}")

    busiest, count = daily_counts.most_common(1)[0]
    print(f"  Busiest day          : {busiest} ({count:,})")

    print("\n  Top 3 longest silences:")
    for i,(g,start,end) in enumerate(compute_gaps(all_datetimes)[:3],1):
        h = int(g.total_seconds()//3600)
        m = int((g.total_seconds()%3600)//60)
        start_ist = start.astimezone(IST)
        end_ist = end.astimezone(IST)
        print(f"    {i}. {h}h {m}m  ({start_ist.strftime('%d %b %Y %I:%M %p')} → {end_ist.strftime('%d %b %Y %I:%M %p')})")

    # ------------------ SESSION METRICS ------------------
    sessions = compute_sessions(messages, gap_threshold_minutes=60)

    total_time = sum((s["duration"] for s in sessions), timedelta())
    durations = [s["duration"] for s in sessions]
    avg_duration = sum(durations, timedelta()) / len(durations) if durations else timedelta()
    longest_session = max(sessions, key=lambda s: s["duration"]) if sessions else None
    busiest_session = max(sessions, key=lambda s: s["msg_count"]) if sessions else None

    # per-sender time: weight each session's duration by that sender's msg share
    sender_time = {s: timedelta() for s in senders}
    for sess in sessions:
        total_msgs_in_sess = sess["msg_count"]
        for s in senders:
            share = sess["sender_counts"].get(s, 0) / total_msgs_in_sess
            sender_time[s] += sess["duration"] * share

    print(f"\nSESSION METRICS  (gap threshold: 60 min)")
    print(f"  Total sessions       : {len(sessions)}")
    print(f"  Total time           : {fmt_duration(total_time)}")
    print(f"  Avg session length   : {fmt_duration(avg_duration)}")
    print(f"  Avg msgs/session     : {sum(s['msg_count'] for s in sessions)/len(sessions):.1f}")

    if longest_session:
        ls_start = longest_session["start"].astimezone(IST)
        print(f"  Longest session      : {fmt_duration(longest_session['duration'])}  ({ls_start.strftime('%d %b %Y')})")

    if busiest_session:
        bs_start = busiest_session["start"].astimezone(IST)
        print(f"  Most msgs in session : {busiest_session['msg_count']}  ({bs_start.strftime('%d %b %Y')})")

    print(f"\n  Time share per person (weighted by message volume):")
    for s in senders:
        print(f"    {s:<20} {fmt_duration(sender_time[s])}")

    print(f"\n  Top 3 longest sessions:")
    top3 = sorted(sessions, key=lambda s: s["duration"], reverse=True)[:3]
    for i, sess in enumerate(top3, 1):
        s_ist = sess["start"].astimezone(IST)
        e_ist = sess["end"].astimezone(IST)
        print(f"    {i}. {fmt_duration(sess['duration'])}  |  {sess['msg_count']} msgs  |  {s_ist.strftime('%d %b %Y %I:%M %p')} → {e_ist.strftime('%I:%M %p')}")

    # ------------------ REST OF REPORT ------------------
    print("\nPER PERSON")
    for s in senders:
        st = stats[s]
        total = st["text_messages"]+st["media_messages"]+st["reactions"]+st["other_messages"]

        avg_resp = sum(response_times[s])/len(response_times[s]) if response_times[s] else 0
        avg_words = len(st["tokens"])/st["text_messages"] if st["text_messages"] else 0

        top_emojis = " ".join(f"{e}×{c}" for e,c in Counter(st["emojis"]).most_common(5))
        top_react = " ".join(f"{e}×{c}" for e,c in st["reaction_types"].most_common(5))

        preview = st["longest_msg_text"][:100].replace("\n"," ")

        print(f"\n  ── {s} ──")
        print(f"    Total events   : {total}")
        print(f"    Text msgs      : {st['text_messages']}")
        print(f"    Media msgs     : {st['media_messages']}")
        print(f"    Reactions      : {st['reactions']}")
        print(f"    Other          : {st['other_messages']}")
        print(f"    Avg response   : {avg_resp/60:.1f} min")
        print(f"    Avg words/msg  : {avg_words:.1f}")
        print(f"    Unique words   : {len(st['unique_tokens'])}")
        print(f"    Emojis         : {len(st['emojis'])}")
        print(f"    Top emojis     : {top_emojis or 'none'}")
        print(f"    Top reactions  : {top_react or 'none'}")
        print(f"    Longest msg    : {st['longest_msg_len']} chars")
        print(f"    Preview        : \"{preview}...\"")

    print("\nTOP WORDS")
    for s in senders:
        print(f"\n  ── {s} ──")
        for i,(w,c) in enumerate(Counter(stats[s]["tokens"]).most_common(20),1):
            print(f"    {i:>2}. {w:<15} {c}")

    print("\nMESSAGES BY DAY")
    mx = max(dow_counts.values()) if dow_counts else 1
    for i,d in enumerate(DAYS_OF_WEEK):
        c = dow_counts.get(i,0)
        print(f"  {d:<10} {c:>5} {'█'*int(c/mx*30)}")

    print("\nMONTHLY VOLUME")
    monthly = Counter()
    for d,c in daily_counts.items():
        monthly[d.strftime("%Y-%m")] += c

    for m in sorted(monthly):
        print(f"  {m} {monthly[m]:>5} {'█'*(monthly[m]//50)}")

    print(f"\n{sep}\nEND\n{sep}")


# ------------------ MAIN ------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python chat_analyzer.py <file.json>")
        sys.exit(1)

    messages = parse_chat(sys.argv[1])
    print(f"Parsed {len(messages):,} events. Running analysis...\n")

    stats, senders, daily_counts, sender_daily, dow_counts, all_datetimes, response_times = analyze(messages)

    print_report(stats, senders, daily_counts, sender_daily, dow_counts, all_datetimes, response_times, messages)

if __name__ == "__main__":
    main()
