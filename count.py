import re
from collections import defaultdict
import emoji

chat_file = "C/home/ankush/Pictures/Important/Diary/New folder/Gargee Basak/Chats/Chat [Whatsapp, till 25th July].txt"

pattern = re.compile(
    r"^\d{2}/\d{2}/\d{4}, .*? - (.*?): (.*)$"
)

stats = defaultdict(lambda: {"chars": 0, "words": 0, "emojis": 0})

with open(chat_file, "r", encoding="utf-8") as f:
    for line in f:
        m = pattern.match(line)
        if not m:
            continue

        person, text = m.groups()

        stats[person]["chars"] += len(text)
        stats[person]["words"] += len(text.split())
        stats[person]["emojis"] += emoji.emoji_count(text)

for person, s in stats.items():
    print(f"\n{person}")
    print(f"  Characters : {s['chars']:,}")
    print(f"  Words      : {s['words']:,}")
    print(f"  Emojis     : {s['emojis']:,}")
