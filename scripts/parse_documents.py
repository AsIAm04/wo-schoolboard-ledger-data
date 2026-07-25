"""
Parse the general document library (agendas/minutes, presentations, budgets, policies,
regulations, CBAs, bids/RFPs, goals, board-member/meeting reference docs) into per-category
JSON files, shaped like meetings-YYYY.json but keyed by category instead of year.

Usage:
    python3 scripts/parse_documents.py ~/Documents/"WOschool docs" .

Output (written to OUT dir):
    documents-<category-slug>.json   -- one per top-level category folder
    documents-categories.json        -- intermediate index of {slug: {category, count, file}}
        (merged into documents-index.json by build_documents_index.py, not written directly
        here, so re-running this script never clobbers the meetings "years" section)

Only .txt files are ingested. Categories that keep paired .pdf/.xls originals already have a
.txt with extracted text sitting next to them -- the source PDFs/spreadsheets are not read.
"""
import os, re, sys, json, glob, hashlib
from collections import defaultdict

if len(sys.argv) < 2:
    print("Usage: python3 parse_documents.py /path/to/'WOschool docs' [output_dir]")
    sys.exit(1)

SRC = sys.argv[1]
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else "."

SKIP_DIRS = set()  # nothing excluded for this pass -- all categories are in scope


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def chunk_text(text, words_per_seg=350):
    words = text.split()
    return [" ".join(words[i:i + words_per_seg]) for i in range(0, len(words), words_per_seg)]


def parse_reference_header(text):
    """Detect the 'Title / Source: URL(s) / Captured: DATE' header convention used for
    small scraped reference pages (BOE-Meeting-Information, Board-Member-Roles). Returns
    (title, source_urls, captured, body) or None if the header isn't present."""
    lines = text.splitlines()
    if len(lines) < 3:
        return None
    title = lines[0].strip()
    if not title or not lines[1].strip().startswith("Source:"):
        return None
    urls = [lines[1].split("Source:", 1)[1].strip()]
    i = 2
    while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith("Captured:"):
        urls.append(lines[i].strip())
        i += 1
    captured = None
    if i < len(lines) and lines[i].strip().startswith("Captured:"):
        captured = lines[i].split("Captured:", 1)[1].strip()
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    body = "\n".join(lines[i:]).strip()
    return title, [u for u in urls if u], captured, body


def extract_date_yyyymmdd(fname):
    m = re.search(r'(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)', fname)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def humanize(stem):
    s = re.sub(r"[_\-]+", " ", stem).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def parse_agendas_minutes(fpath, fname, subfolder, text):
    date = extract_date_yyyymmdd(fname)
    doc_type = "Minutes" if "minutes" in fname.lower() else ("Agenda" if "agenda" in fname.lower() else "Agendas-Minutes")
    title = f"Board Meeting {doc_type}" + (f" — {date}" if date else f" ({subfolder})")
    return title, date, doc_type


def parse_bylaws(fpath, fname, subfolder, text):
    m = re.match(r'(Polic(?:y|ies)|Regulations?)_(\d+(?:\.\d+)?)_(.+)', fname[:-4], re.IGNORECASE)
    if m:
        series, rest = m.group(2), humanize(m.group(3))
        title = f"{subfolder} {series} — {rest}"
    else:
        title = f"{subfolder}: {humanize(fname[:-4])}"
    return title, None, subfolder


def parse_generic(fpath, fname, subfolder, text):
    stem = fname[:-4] if fname.lower().endswith(".txt") else fname
    date = extract_date_yyyymmdd(fname)
    title = humanize(stem)
    return title, date, None


CATEGORY_PARSERS = {
    "Agendas-Minutes": parse_agendas_minutes,
    "Bylaws-Policies-Regulations": parse_bylaws,
}


def process_file(category, subfolder, fpath, fname):
    with open(fpath, "r", errors="ignore") as f:
        raw = f.read()

    ref = parse_reference_header(raw)
    source_url, captured = None, None
    if ref:
        title, urls, captured, body = ref
        source_url = urls[0] if urls else None
        text = body
        doc_type = "Reference"
        date = captured
    else:
        parser = CATEGORY_PARSERS.get(category, parse_generic)
        title, date, doc_type = parser(fpath, fname, subfolder, raw)
        text = raw.strip()

    word_count = len(text.split())
    segs = chunk_text(text) if text else []
    relpath = os.path.relpath(fpath, SRC)
    uid = hashlib.sha1(relpath.encode()).hexdigest()[:10]

    return {
        "id": f"doc-{slugify(category)}-{uid}",
        "category": category,
        "subcategory": subfolder,
        "title": title,
        "date": date,
        "doc_type": doc_type,
        "source_url": source_url,
        "captured": captured,
        "source_filename": fname,
        "relpath": relpath,
        "word_count": word_count,
        "segment_count": len(segs),
        "segments": segs,
    }


def main():
    by_category = defaultdict(list)
    empty_files = []

    for category in sorted(os.listdir(SRC)):
        cat_path = os.path.join(SRC, category)
        if not os.path.isdir(cat_path) or category in SKIP_DIRS or category.startswith("."):
            continue
        for root, dirs, files in os.walk(cat_path):
            subfolder = os.path.relpath(root, cat_path)
            subfolder = None if subfolder == "." else subfolder
            for fname in sorted(files):
                if not fname.lower().endswith(".txt"):
                    continue
                fpath = os.path.join(root, fname)
                rec = process_file(category, subfolder, fpath, fname)
                if rec["word_count"] == 0:
                    empty_files.append(rec["relpath"])
                    continue
                by_category[category].append(rec)

    os.makedirs(OUT_DIR, exist_ok=True)
    cat_index = {}
    total = 0
    for category, records in sorted(by_category.items()):
        records.sort(key=lambda r: (r["date"] or "", r["source_filename"]))
        slug = slugify(category)
        fname = f"documents-{slug}.json"
        with open(os.path.join(OUT_DIR, fname), "w") as f:
            json.dump({"documents": records}, f, indent=2)
        cat_index[slug] = {"category": category, "count": len(records), "file": fname}
        total += len(records)

    with open(os.path.join(OUT_DIR, "documents-categories.json"), "w") as f:
        json.dump({"categories": cat_index, "total_documents": total}, f, indent=2)

    print(f"Parsed {total} documents across {len(by_category)} categories")
    for slug, info in cat_index.items():
        print(f"  {info['category']:35s} {info['count']:4d}  -> {info['file']}")
    if empty_files:
        print(f"\nSkipped {len(empty_files)} empty .txt files (no extracted text):")
        for f in empty_files:
            print("  EMPTY:", f)


if __name__ == "__main__":
    main()
