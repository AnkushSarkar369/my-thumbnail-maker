import json
from collections import Counter, defaultdict

def analyze_json(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = data.get("messages", [])

    type_counter = Counter()
    key_counter = Counter()
    type_examples = defaultdict(list)

    for msg in data:
        msg_type = msg.get("type", "UNKNOWN")
        type_counter[msg_type] += 1

        for key in msg.keys():
            key_counter[key] += 1

        # store a few examples per type
        if len(type_examples[msg_type]) < 3:
            type_examples[msg_type].append(msg)

    print("\n" + "="*60)
    print("TYPE DISTRIBUTION")
    print("="*60)
    for t, count in type_counter.most_common():
        print(f"{t:<15} {count:,}")

    print("\n" + "="*60)
    print("KEY USAGE")
    print("="*60)
    for k, count in key_counter.most_common():
        print(f"{k:<20} {count:,}")

    print("\n" + "="*60)
    print("TYPE STRUCTURE SAMPLES")
    print("="*60)

    for t, examples in type_examples.items():
        print(f"\n--- TYPE: {t} ---")
        for ex in examples:
            print({k: ex[k] for k in ex if k != "mediaUrl"})  # avoid huge spam

    print("\nDone.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python analyze_schema.py <file.json>")
        exit()

    analyze_json(sys.argv[1])