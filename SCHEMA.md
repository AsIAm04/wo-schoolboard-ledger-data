# wo-schoolboard-ledger-data — schema

Companion data repo for the West Orange Board of Education ledger (sibling to `wo-ledger-data`,
which covers Township Council). Source material is full meeting transcripts, not scraped PDFs,
so there is no OCR/extraction step here — every record already has complete text.

## Files

- `meetings-YYYY.json` — array of meeting records for that calendar year (one file per year,
  2021–2026 currently).
- `documents-index.json` — summary index: per-year record counts and type breakdowns. The site
  reads this first to know which year files to fetch.

## Record shape (`meetings-YYYY.json`)

```json
{
  "id": "boe-2025-3f9a2c1b8e",
  "title": "West Orange Board of Education Meeting, Monday, 8/18/2025 @ 6:30pm",
  "date": "2025-08-18",
  "year": 2025,
  "types": ["Regular"],
  "source_filename": "West Orange Board of Education Meeting, Monday, 8⧸18⧸2025 @ 6_30pm.txt",
  "source_folder": "2025 Board of Education Meetings",
  "word_count": 14203,
  "segment_count": 41,
  "segments": ["...", "..."]
}
```

- `id` — stable id, `boe-<year>-<10 char hash of source filename>`.
- `types` — one or more of: `Regular`, `Reorganization`, `Special`, `Budget`, `Policy Workshop`,
  `Goal Setting`, `Virtual`, `Parade of Honors`, `Training`, `Report`. Derived from the meeting
  title by keyword match — not board-confirmed, so treat as a filter convenience, not an
  authoritative record type.
- `segments` — the transcript chunked into ~350-word pieces, in order. This is the citation unit:
  when Ask AI answers a question it cites `(meeting id, segment N)` the same way the Township
  Ledger cites `(meeting, page N)` for scanned PDFs. Full transcript = `segments.join(" ")`.
- One record (`Day in the Life of Our Students - Superintendent Report`) has `date: null` — it's a
  standalone report, not a dated board meeting. It's indexed and searchable but excluded from
  date-sorted browsing.

## Known gaps / caveats

- Transcripts appear to be auto-generated (no speaker diarization, no timestamps, occasional
  filler like repeated "you" at the top from silence/audio artifacts). Treat as unofficial,
  same caveat the Township Ledger puts on its OCR text.
- A few source filenames had no year in them (e.g. `Mon., 7/26 @ 7:30pm`); year was inferred from
  the containing folder. One misfiled transcript (`12/20/2023` meeting saved in the 2022 folder)
  was correctly reassigned to 2023 by parsed date, not folder location.
- No resolutions/policy-vote index yet — this repo is meetings-only for now, the BOE equivalent
  of council resolutions/ordinances would need separate source documents (board policy manual,
  vote records) if you want that section built later.
