"""
Correct misheard proper names in auto-generated meeting transcripts.

Every correction is one token for one token (or one phrase for a phrase with the same word
count), so segment boundaries and segment indices never shift. That keeps every existing
Ask AI citation (meeting id, segment N) pointing at the same text it did before.

Spellings are verified against the district's own minutes (documents-agendas-minutes.json),
Township Council records (wo-ledger-data) for council members, and public sources for
state officials. See NOTES at the bottom for what was deliberately left alone.

Usage:
    # repo JSON (meetings-YYYY.json + transcript-memos*.json), in place:
    python3 scripts/fix_names.py json .

    # local source .txt folders, in place (so a rebuild with parse.py keeps the fixes).
    # Only folders named "20XX Board of Education Meeting(s)" are touched:
    python3 scripts/fix_names.py txt ~/Documents/"WO school board"

    # dry run either mode (prints counts, writes nothing):
    python3 scripts/fix_names.py json . --dry-run
"""
import os, re, sys, json, glob
from collections import Counter

# ---- Global token fixes: unambiguous misspellings of verified names -------------------------
TOKEN_MAP = {
    # Robert Ivker (board member; Vice President 2025)
    "Ifker": "Ivker", "Ipker": "Ivker", "Ipiker": "Ivker", "Ripker": "Ivker", "Rivker": "Ivker",
    "Iverker": "Ivker", "Ibker": "Ivker", "Ivaker": "Ivker", "Iker": "Ivker",
    # Trenae Lambkin (assistant / acting business administrator)
    "Lampkin": "Lambkin", "Lamkin": "Lambkin", "Trinae": "Trenae",
    # Former board member/president Tunnicliffe
    "Tonecliffe": "Tunnicliffe", "Tennecliffe": "Tunnicliffe", "Tonicliff": "Tunnicliffe",
    "Tunnicliff": "Tunnicliffe", "Tunicliff": "Tunnicliffe", "Sunnycliffe": "Tunnicliffe",
    "Tonicliffe": "Tunnicliffe", "Turnicliffe": "Tunnicliffe", "Tenneycliffe": "Tunnicliffe",
    "Tunnycliffe": "Tunnicliffe", "Tunicliffe": "Tunnicliffe", "Sunnycliff": "Tunnicliffe",
    # Former board member/vice president Rothstein
    "Rolstein": "Rothstein", "Rothson": "Rothstein", "Rothsteinstein": "Rothstein",
    "Mrothstein": "Rothstein",
    # Former board member/vice president Huerta
    "Huerto": "Huerta", "Suerta": "Huerta", "Fuerta": "Huerta", "Suerata": "Huerta",
    "Werta": "Huerta", "Herta": "Huerta",
    # Eric Stevenson
    "Stevensonson": "Stevenson", "Stevensona": "Stevenson",
    # Strauss Esmay (policy service)
    "Essamay": "Esmay", "Essmy": "Esmay",
    # Josh Goldfarb (WOEA president), Badlani family / Nikhil Badlani Foundation
    "Goldbarb": "Goldfarb", "Bhatlani": "Badlani", "Badalani": "Badlani",
    # Oscar Guerrero (WOHS principal), Nicholas Munoz (facilities)
    "Guerrera": "Guerrero", "Muniz": "Munoz",
}

# ---- Context fixes: same word count, only where the speaker is certain ----------------------
# (date filter, old phrase, new phrase). date filter is a (start, end) ISO range or None.
CONTEXT_FIXES = [
    # Brian Rock: board member from 2022, president from January 2023
    (("2023-01-01", "2099-12-31"), "President Roth", "President Rock"),
    (("2023-01-01", "2099-12-31"), "President Rauch", "President Rock"),
    (("2023-03-08", "2023-03-08"), "President Rothkopf", "President Rock"),
    (("2023-08-28", "2023-08-28"), "President Ruck", "President Rock"),
    (("2022-02-28", "2022-02-28"), "Mr. Roth? Yes.", "Mr. Rock? Yes."),
    (("2022-02-28", "2022-02-28"), "Mr. Roth? Sure.", "Mr. Rock? Sure."),
    (("2022-05-09", "2022-05-09"), "Mrs. Huerta? Yes. Mr. Roth? Yes.", "Mrs. Huerta? Yes. Mr. Rock? Yes."),
    (("2022-05-23", "2022-05-23"), "Mrs. Rata. Yes. Mr. Roth. Yes.", "Mrs. Huerta. Yes. Mr. Rock. Yes."),
    (("2022-05-23", "2022-05-23"), "Mrs. Huerta? Yes. Mr. Roth? Yes.", "Mrs. Huerta? Yes. Mr. Rock? Yes."),
    (("2022-12-19", "2022-12-19"), "Ms. Huerta? Yes. Mr. Roth? Yes.", "Ms. Huerta? Yes. Mr. Rock? Yes."),
    (("2026-01-06", "2026-01-06"), "Mr. Roth yes", "Mr. Rock yes"),
    (("2026-04-20", "2026-04-20"), "Point of order, Mr. Roth.", "Point of order, Mr. Rock."),
    # Rothstein was vice president in early 2022
    (("2022-02-07", "2022-02-07"), "Vice President Roth seemingly", "Vice President Rothstein seemingly"),
    # Huerta, 2021 roll calls
    (("2021-01-01", "2021-12-31"), "Mrs. Berta?", "Mrs. Huerta?"),
    # Maria Vera chaired 9/16/2024 with President Rock absent
    (("2024-09-16", "2024-09-16"), "President Rivera", "President Vera"),
    # Dr. Dia Bryant, on the board from March 2024
    (("2024-03-01", "2099-12-31"), "Dr. Brian", "Dr. Bryant"),
    (("2024-03-01", "2099-12-31"), "Dr. Bryan", "Dr. Bryant"),
    (("2024-03-01", "2099-12-31"), "Dr Brian", "Dr Bryant"),
    (("2024-03-01", "2099-12-31"), "dr bryan", "dr Bryant"),
    # Eric Stevenson
    (None, "Mr. Stephenson", "Mr. Stevenson"),
    (None, "Mr. Stevensons", "Mr. Stevenson"),
]

TOKEN_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, TOKEN_MAP), key=len, reverse=True)) + r")\b")
MEETING_DIR_RE = re.compile(r"^20\d\d Board of Education Meetings?$", re.I)


def in_range(date, rng):
    if rng is None:
        return True
    return bool(date) and rng[0] <= date <= rng[1]


def fix_text(text, date, counts):
    def tok(m):
        counts[(m.group(1), TOKEN_MAP[m.group(1)])] += 1
        return TOKEN_MAP[m.group(1)]
    text = TOKEN_RE.sub(tok, text)
    for rng, old, new in CONTEXT_FIXES:
        if not in_range(date, rng):
            continue
        assert len(old.split()) == len(new.split()), (old, new)
        # whole-phrase match only, so "President Roth" never touches "President Rothstein"
        # and "Dr. Bryan" never touches "Dr. Bryant"
        pat = re.compile(r"(?<!\w)" + re.escape(old) + r"(?!\w)")
        text, n = pat.subn(new, text)
        if n:
            counts[(old, new)] += n
    return text


def fix_record(rec, counts):
    before = [len(s.split()) for s in rec["segments"]]
    rec["segments"] = [fix_text(s, rec.get("date"), counts) for s in rec["segments"]]
    after = [len(s.split()) for s in rec["segments"]]
    assert before == after, f"word count changed in {rec['id']}"
    return rec


def run_json(root, dry):
    counts = Counter()
    date_by_id = {}
    for path in sorted(glob.glob(os.path.join(root, "meetings-*.json"))):
        recs = [fix_record(r, counts) for r in json.load(open(path))]
        for r in recs:
            date_by_id[r["id"]] = r.get("date")
        if not dry:
            with open(path, "w") as f:
                json.dump(recs, f, indent=2, ensure_ascii=False)
                f.write("\n")
    for name in ("transcript-memos.json", "transcript-memos-es.json"):
        path = os.path.join(root, name)
        if not os.path.exists(path):
            continue
        memos = json.load(open(path))
        memos = {k: fix_text(v, date_by_id.get(k), counts) if isinstance(v, str) else v
                 for k, v in memos.items()}
        if not dry:
            with open(path, "w") as f:
                json.dump(memos, f, indent=2, ensure_ascii=False)
    return counts


def date_from_filename(fname):
    m = re.search(r"(\d{1,2})[/⧸](\d{1,2})[/⧸](\d{2,4})", fname)
    if not m:
        return None
    y = int(m.group(3)); y = y + 2000 if y < 100 else y
    return f"{y:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def run_txt(root, dry):
    counts = Counter()
    dirs = [d for d in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, d)) and MEETING_DIR_RE.match(d)]
    if not dirs:
        sys.exit(f"No '20XX Board of Education Meeting(s)' folders found in {root}")
    print("folders:", ", ".join(dirs))
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(root, d, "*.txt"))):
            text = open(path, errors="ignore").read()
            fixed = fix_text(text, date_from_filename(os.path.basename(path)), counts)
            if fixed != text and not dry:
                with open(path, "w") as f:
                    f.write(fixed)
    return counts


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("json", "txt"):
        print(__doc__); sys.exit(1)
    dry = "--dry-run" in sys.argv
    counts = (run_json if sys.argv[1] == "json" else run_txt)(os.path.expanduser(sys.argv[2]), dry)
    for (old, new), n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"{n:5d}  {old!r} -> {new!r}")
    print("total", sum(counts.values()), "(dry run)" if dry else "")

NOTES = """
Left alone on purpose (ambiguous without the recording):
- "Mr. Roth" in 2021 and in non-roll-call 2022 remarks: could be Mr. Rock or Mr. Rothstein.
- "Mr. Rothschild" (2021-02-03), "Mr. Huerf" (2023-08-28), "Melinda Urta" (2024-01-04).
- "Vice President Tenne Cliffs" (2021-12-06): fixing it changes the word count.
- "Brian" on its own: Brian Rock's first name; only "Dr. Brian/Bryan" is changed.
- Residents' names at public comment, unless official minutes list them.
"""
