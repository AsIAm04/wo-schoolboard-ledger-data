#!/usr/bin/env node
// Backfill script: generates structured meeting memos for every Agendas-Minutes
// meeting date NOT already covered in meeting-memos.json, by calling your live
// Cloudflare Worker (this must be run somewhere that can reach it -- your machine,
// not a sandboxed environment). Merges results into meeting-memos.json in place.
//
// Usage:
//   node backfill-meeting-memos.mjs
//
// Requires Node 18+ (built-in fetch). No npm install needed.
//
// Reads:  ./meeting-memos.json (created if missing)
// Writes: ./meeting-memos.json (updated in place, one date at a time so a crash
//         partway through doesn't lose completed work)

import { readFileSync, writeFileSync, existsSync } from "fs";

const DATA_BASE = "https://raw.githubusercontent.com/AsIAm04/wo-schoolboard-ledger-data/main";
const WORKER_BASE = "https://wo-schoolboard-ledger-ask.lumiere4.workers.dev";
const MEMOS_FILE = "./meeting-memos.json";
const DELAY_MS = 1500; // be polite to the Worker -- adjust if you hit rate limits

const MEMO_SECTION_HEADERS = [
  "Title & Date", "Attendees", "Purpose & Objectives", "Key Discussion Points",
  "Decisions Made", "Action Items", "Unresolved Issues", "Next Steps"
];

function buildMemoPrompt({ dateLabel, sourceText, sourceDescription }) {
  return `You are producing a scannable memo from ${sourceDescription} for a West Orange, NJ resident. ${dateLabel ? "The date is " + dateLabel + ". " : ""}Using ONLY the text provided below, produce a memo with these sections, in this exact order, using this exact header text:

Title & Date
Attendees
Purpose & Objectives
Key Discussion Points
Decisions Made
Action Items
Unresolved Issues
Next Steps

Rules:
- Use only what's stated in the source text. If a section has nothing to report, write "None noted in the record" -- never invent attendees, decisions, owners, or deadlines.
- For the Action Items section, list one item per line as "- task -- owner -- deadline", using the exact date/format given in the source for deadline, or "Not specified" if none is given. If there are none, write "None noted in the record" instead of a list.
- Keep the whole memo concise -- short bullets, not paragraphs, except Purpose & Objectives and Next Steps which may be 1-2 sentences.
- The source may contain auto-generated transcription errors or PDF-extraction artifacts -- work around obvious garbling rather than repeating it verbatim.
- Do not output the source text itself, and do not mention that you were given an excerpt.
- Do not use markdown formatting anywhere -- no **bold**, no # headings, no numbered lists. Section header lines must be the exact plain text shown above with nothing else on that line (no asterisks, no trailing colon).
SOURCE TEXT:
${sourceText}`;
}

function fmtDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
}

async function callMemoWorker(systemPrompt) {
  const res = await fetch(WORKER_BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "ask", system: systemPrompt, question: "Produce the memo." })
  });
  const data = await res.json();
  if (!res.ok || data.error) {
    const rawError = data && data.error !== undefined ? data.error : `HTTP ${res.status}`;
    const headline = typeof rawError === "string" ? rawError : JSON.stringify(rawError);
    // Surface every provider's attempt (not just the first/headline one) so failures further
    // down the fallback chain (Gemini, Workers AI, OpenRouter) aren't hidden.
    const attempts = data && data.debug && Array.isArray(data.debug.attempts) ? data.debug.attempts : null;
    const detail = attempts ? "\n  - " + attempts.join("\n  - ") : "";
    throw new Error(headline + detail);
  }
  const textBlocks = (data.content || []).filter(b => b.type === "text").map(b => b.text);
  return textBlocks.join("\n\n").trim();
}

function looksLikeValidMemo(text) {
  // Cheap sanity check: all 8 headers must appear on their own line. Mirrors the tolerant
  // cleanup the site's own parser applies (parseMemoSections in wosb-source-clean.html) so this
  // check doesn't falsely flag a memo the site would actually parse fine, or vice versa.
  const lines = text.split("\n").map(l =>
    l.trim().replace(/\*\*/g, "").replace(/^#+\s*/, "").replace(/[:#*]+$/, "").trim().toLowerCase()
  );
  return MEMO_SECTION_HEADERS.every(h => lines.includes(h.toLowerCase()));
}

async function main() {
  console.log("Fetching Agendas-Minutes document index...");
  const res = await fetch(`${DATA_BASE}/documents-agendas-minutes.json`);
  if (!res.ok) throw new Error(`Failed to fetch documents-agendas-minutes.json: HTTP ${res.status}`);
  const data = await res.json();
  const docs = Array.isArray(data) ? data : data.documents || [];

  const byDate = new Map();
  for (const d of docs) {
    const key = d.date || "unknown";
    if (!byDate.has(key)) byDate.set(key, {});
    byDate.get(key)[d.doc_type === "Minutes" ? "minutes" : "agenda"] = d;
  }

  let memos = {};
  if (existsSync(MEMOS_FILE)) {
    memos = JSON.parse(readFileSync(MEMOS_FILE, "utf8"));
    console.log(`Loaded existing ${MEMOS_FILE} with ${Object.keys(memos).length} memos already present.`);
  }

  const allDates = [...byDate.keys()].filter(k => k !== "unknown").sort((a, b) => b.localeCompare(a));
  const remaining = allDates.filter(dt => !memos[dt]);
  console.log(`${allDates.length} total meeting dates, ${remaining.length} still need memos.`);

  for (const dateKey of remaining) {
    const pair = byDate.get(dateKey);
    const parts = [];
    for (const kind of ["agenda", "minutes"]) {
      const doc = pair[kind];
      if (!doc || !doc.segments || !doc.segments.length) continue;
      parts.push(`=== ${kind.toUpperCase()} ===\n` + doc.segments.join("\n\n"));
    }
    const sourceText = parts.join("\n\n").slice(0, 9000);
    if (!sourceText) {
      console.log(`[skip] ${dateKey}: no segment text available`);
      continue;
    }
    console.log(`[generating] ${dateKey}...`);
    try {
      const prompt = buildMemoPrompt({
        dateLabel: fmtDate(dateKey),
        sourceText,
        sourceDescription: "the official agenda and/or minutes of a West Orange Board of Education meeting"
      });
      const memo = await callMemoWorker(prompt);
      if (!looksLikeValidMemo(memo)) {
        console.warn(`[warn] ${dateKey}: memo is missing expected section headers -- saved anyway, please review manually.`);
      }
      memos[dateKey] = memo;
      writeFileSync(MEMOS_FILE, JSON.stringify(memos, null, 2), "utf8");
      console.log(`[done] ${dateKey} -- saved to ${MEMOS_FILE}`);
    } catch (err) {
      console.error(`[error] ${dateKey}: ${err.message} -- skipping, will retry next run`);
    }
    await new Promise(r => setTimeout(r, DELAY_MS));
  }

  console.log(`\nFinished. ${Object.keys(memos).length}/${allDates.length} meeting dates now have memos in ${MEMOS_FILE}.`);
  console.log("Commit this file to the wo-schoolboard-ledger-data repo so the site picks it up.");
}

main().catch(err => {
  console.error("Fatal error:", err);
  process.exit(1);
});
