import os, re, json, glob, hashlib

import sys
if len(sys.argv) < 2:
    print("Usage: python3 parse.py /path/to/'WO school board' [output_dir]")
    print("Example: python3 parse.py ~/Documents/'WO school board' ../data")
    sys.exit(1)
SRC = sys.argv[1]
YEAR_DIRS = [
    "2021 Board of Educational Meetings",
    "2022 Board of Education Meetings",
    "2023 Board of Education Meetings",
    "2024 Board of Education Meetings",
    "2025 Board of Education Meetings",
    "2026 Board of Education meetings",
]

def parse_filename(fname):
    base = fname[:-4]
    base = base.replace("⧸", "/")
    title = base.strip()

    date = None
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', base)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
        y = int(y)
        if y < 100:
            y += 2000
        try:
            date = f"{y:04d}-{mo:02d}-{d:02d}"
        except:
            date = None
    if not date:
        m2 = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?', base, re.IGNORECASE)
        if m2:
            monthnames = ["January","February","March","April","May","June","July","August","September","October","November","December"]
            mo = monthnames.index(m2.group(1).capitalize()) + 1
            d = int(m2.group(2))
            y = m2.group(3)
            date = (int(y), mo, d) if y else (None, mo, d)

    if not date:
        # bare M/D with no year (e.g. "7/26" or "8/16")
        m3 = re.search(r'(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)', base)
        if m3:
            mo, d = int(m3.group(1)), int(m3.group(2))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                date = (None, mo, d)

    return title, date

def classify_type(title):
    t = title.lower()
    types = []
    if "reorg" in t or "re-org" in t:
        types.append("Reorganization")
    if "special" in t:
        types.append("Special")
    if "budget" in t:
        types.append("Budget")
    if "policy workshop" in t:
        types.append("Policy Workshop")
    if "goal setting" in t:
        types.append("Goal Setting")
    if "virtual" in t:
        types.append("Virtual")
    if "parade of honors" in t:
        types.append("Parade of Honors")
    if "training" in t:
        types.append("Training")
    if not types:
        types.append("Regular")
    return types

def chunk_text(text, words_per_seg=350):
    words = text.split()
    segs = []
    for i in range(0, len(words), words_per_seg):
        seg_words = words[i:i+words_per_seg]
        segs.append(" ".join(seg_words))
    return segs

records = []
skipped = []

for d in YEAR_DIRS:
    dirpath = os.path.join(SRC, d)
    if not os.path.isdir(dirpath):
        skipped.append((d, "missing dir"))
        continue
    for fname in sorted(os.listdir(dirpath)):
        if not fname.endswith(".txt"):
            continue
        fpath = os.path.join(dirpath, fname)
        with open(fpath, "r", errors="ignore") as f:
            text = f.read().strip()

        title, date_raw = parse_filename(fname)
        folder_year = int(re.match(r'(\d{4})', d).group(1))

        date_str = None
        if isinstance(date_raw, str):
            date_str = date_raw
        elif isinstance(date_raw, tuple):
            y, mo, dd = date_raw
            if y is None:
                y = folder_year
            date_str = f"{y:04d}-{mo:02d}-{dd:02d}"

        year = int(date_str[:4]) if date_str else folder_year
        is_report = "report" in title.lower() and "meeting" not in title.lower()
        rec_types = ["Report"] if is_report else classify_type(title)
        word_count = len(text.split())
        segs = chunk_text(text) if text else []
        uid = hashlib.sha1(fname.encode()).hexdigest()[:10]

        rec = {
            "id": f"boe-{year}-{uid}",
            "title": title,
            "date": date_str,
            "year": year,
            "types": rec_types,
            "source_filename": fname,
            "source_folder": d,
            "word_count": word_count,
            "segment_count": len(segs),
            "segments": segs,
        }
        records.append(rec)

print(f"Parsed {len(records)} records")
missing_date = [r["source_filename"] for r in records if r["date"] is None]
print(f"Missing date: {len(missing_date)}")
for f in missing_date:
    print("  NO DATE:", f)
print("Skipped dirs:", skipped)

OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else "./_intermediate"
os.makedirs(OUT_DIR, exist_ok=True)
with open(os.path.join(OUT_DIR, "all_records.json"), "w") as f:
    json.dump(records, f, indent=2)
