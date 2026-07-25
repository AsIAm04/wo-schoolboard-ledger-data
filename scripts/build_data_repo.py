import json, os
from collections import defaultdict

import sys
INTERMEDIATE = sys.argv[1] if len(sys.argv) > 1 else "./_intermediate/all_records.json"
recs = json.load(open(INTERMEDIATE))

OUT = sys.argv[2] if len(sys.argv) > 2 else "."
os.makedirs(OUT, exist_ok=True)

by_year = defaultdict(list)
for r in recs:
    by_year[r["year"]].append(r)

index = {"years": {}, "total_meetings": len(recs), "generated_from": "raw meeting transcripts (.txt)"}

for year, items in sorted(by_year.items()):
    items.sort(key=lambda r: (r["date"] or "9999-99-99"))
    fname = f"meetings-{year}.json"
    with open(os.path.join(OUT, fname), "w") as f:
        json.dump(items, f, indent=2)
    type_counts = defaultdict(int)
    for it in items:
        for t in it["types"]:
            type_counts[t] += 1
    index["years"][str(year)] = {
        "count": len(items),
        "file": fname,
        "type_counts": dict(type_counts),
    }

with open(os.path.join(OUT, "documents-index.json"), "w") as f:
    json.dump(index, f, indent=2)

print(json.dumps(index, indent=2))
