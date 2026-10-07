#!/usr/bin/env node
/**
 * Batch-generates pre-built transcript memos for the School Board Ledger site's Browse Meetings
 * tab, using a LOCAL model via Ollama -- no API key, no cloud call. Same approach as
 * generate-memos-local.js (which did this for Town Ledger), adapted to wo-schoolboard-ledger-
 * data's schema.
 *
 * This is the missing piece that makes the widget's "Preparing memo…" (on-demand generation)
 * become instant: the widget already checks for a `transcript-memos.json` file at the repo root
 * before falling back to live generation -- this script is what builds that file.
 *
 * SETUP: same Ollama setup as before -- if you already ran generate-memos-local.js for Town
 * Ledger, you're already set up (same `ollama serve` + `ollama pull llama3.1:8b`).
 *
 * WHERE TO RUN THIS: from the root of your local clone of wo-schoolboard-ledger-data.
 *   node generate-transcript-memos-local.js                       # all years, llama3.1:8b
 *   node generate-transcript-memos-local.js 2023,2024 qwen2.5:14b  # specific years + model
 *
 * Requires Node 18+ (built-in fetch, no npm install).
 *
 * Resumable: writes transcript-memos.json incrementally, skips any meeting id already present on
 * the next run.
 */

const fs = require('fs');
const path = require('path');

const OLLAMA_URL = process.env.OLLAMA_URL || 'http://localhost:11434';
const YEARS_ARG = process.argv[2] || null; // null = all years found in documents-index.json
const MODEL = process.argv[3] || 'llama3.1:8b';
const CONCURRENCY = 1;
const RETRY_ATTEMPTS = 2;
const RETRY_DELAY_MS = 3000;
const REQUEST_TIMEOUT_MS = 180000;

const DATA_DIR = process.cwd();
const TRANSCRIPT_MEMOS_PATH = path.join(DATA_DIR, 'transcript-memos.json');
const INDEX_PATH = path.join(DATA_DIR, 'documents-index.json');

function fmtDate(iso) {
  if (!iso) return 'Date unknown';
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return iso;
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
}

function primaryType(m) {
  return (m.types && m.types[0]) || 'Regular';
}

function budgetedText(text, limit) {
  const joined = text || '';
  if (joined.length <= limit) return joined;
  return joined.slice(0, limit) + '\n\n[Note: source text truncated here for length. The source document continues beyond this point.]';
}

// ---- Exact same prompt as the live widget's buildMemoPrompt -- kept byte-for-byte identical so
// a pre-generated memo reads the same as one generated live. ----
function buildMemoPrompt({ dateLabel, sourceText, sourceDescription }) {
  return `You are producing a scannable memo from ${sourceDescription} for a West Orange, NJ resident. ${dateLabel ? 'The date is ' + dateLabel + '. ' : ''}Using ONLY the text provided below, produce a memo with these sections, in this exact order, using this exact header text:
Title & Date
Attendees
Purpose & Objectives
Key Discussion Points
Decisions Made
Action Items
Unresolved Issues
Next Steps
Rules:
- Each header above must appear alone on its own line, with nothing else on that line.
- Use only what's stated in the source text. If a section has nothing to report, write "None noted in the record" -- never invent attendees, decisions, owners, or deadlines.
- For the Action Items section, list one item per line as "- task -- owner -- deadline", using the exact date/format given in the source for deadline, or "Not specified" if none is given. If there are none, write "None noted in the record" instead of a list.
- Keep the whole memo concise -- short bullets, not paragraphs, except Purpose & Objectives and Next Steps which may be 1-2 sentences.
- The source may contain auto-generated transcription errors or PDF-extraction artifacts -- work around obvious garbling rather than repeating it verbatim.
- If the source text ends with a truncation note, do not describe the record as complete -- work only from what's actually present, and write "None noted in the record" for anything you can't confirm rather than guessing at the missing portion.
- Do not output the source text itself, and do not mention that you were given an excerpt.
- Do not use markdown formatting anywhere -- no **bold**, no # headings, no numbered lists. Section header lines must be the exact plain text shown above with nothing else on that line (no asterisks, no trailing colon).
SOURCE TEXT:
${sourceText}`;
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function callOllama(systemPrompt) {
  let res;
  try {
    res = await fetchWithTimeout(`${OLLAMA_URL}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: MODEL,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: 'Produce the memo.' }
        ],
        stream: false
      })
    }, REQUEST_TIMEOUT_MS);
  } catch (e) {
    if (e.name === 'AbortError') throw new Error(`Timed out after ${REQUEST_TIMEOUT_MS / 1000}s -- the model may be too large/slow for this machine.`);
    if (e.cause && e.cause.code === 'ECONNREFUSED') {
      throw new Error(`Can't reach Ollama at ${OLLAMA_URL}. Is it running? Try: ollama serve`);
    }
    throw new Error(`Failed to reach Ollama: ${e.message}`);
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    if (res.status === 404) throw new Error(`Model "${MODEL}" isn't pulled yet. Run: ollama pull ${MODEL}`);
    throw new Error(`Ollama HTTP ${res.status}: ${body}`);
  }
  const data = await res.json();
  const text = (data && data.message && data.message.content || '').trim();
  if (!text) throw new Error('Ollama returned empty text');
  return text;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function withRetry(fn, label) {
  let lastErr;
  for (let attempt = 1; attempt <= RETRY_ATTEMPTS; attempt++) {
    try { return await fn(); }
    catch (e) {
      lastErr = e;
      console.warn(`  [retry ${attempt}/${RETRY_ATTEMPTS}] ${label}: ${e.message}`);
      if (/Can't reach Ollama|isn't pulled yet/.test(e.message)) throw e;
      if (attempt < RETRY_ATTEMPTS) await sleep(RETRY_DELAY_MS);
    }
  }
  throw lastErr;
}

function loadJsonSafe(p) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); }
  catch (e) { return {}; }
}

async function runPool(items, worker, concurrency) {
  let i = 0;
  let completed = 0;
  async function next() {
    while (i < items.length) {
      const idx = i++;
      const item = items[idx];
      const start = Date.now();
      try {
        await worker(item);
      } catch (e) {
        console.error(`FAILED (giving up after retries): ${item.__label} -- ${e.message}`);
      }
      completed++;
      const secs = ((Date.now() - start) / 1000).toFixed(1);
      console.log(`  [${completed}/${items.length}] ${item.__label} (${secs}s)`);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, next));
}

async function checkOllamaReachable() {
  try {
    const res = await fetchWithTimeout(`${OLLAMA_URL}/api/tags`, {}, 5000);
    return res.ok;
  } catch (e) {
    return false;
  }
}

async function main() {
  console.log(`Model: ${MODEL}`);

  const reachable = await checkOllamaReachable();
  if (!reachable) {
    console.error(`\nCan't reach Ollama at ${OLLAMA_URL}.`);
    console.error('  ollama serve          (in another terminal tab, if not already running)');
    console.error(`  ollama pull ${MODEL}`);
    process.exit(1);
  }

  if (!fs.existsSync(INDEX_PATH)) {
    console.error(`Can't find documents-index.json in ${DATA_DIR}. Run this from the root of your wo-schoolboard-ledger-data clone.`);
    process.exit(1);
  }
  const index = JSON.parse(fs.readFileSync(INDEX_PATH, 'utf8'));
  const allYears = Object.keys(index.years || {});
  const years = YEARS_ARG ? YEARS_ARG.split(',').map(s => s.trim()) : allYears;
  console.log(`Years: ${years.join(', ')}`);

  const transcriptMemos = loadJsonSafe(TRANSCRIPT_MEMOS_PATH);
  console.log(`Existing transcript-memos.json: ${Object.keys(transcriptMemos).length} entries`);

  let allMeetings = [];
  for (const y of years) {
    const yearInfo = index.years[y];
    if (!yearInfo) { console.warn(`No entry for year ${y} in documents-index.json, skipping.`); continue; }
    const file = path.join(DATA_DIR, yearInfo.file);
    if (!fs.existsSync(file)) { console.warn(`Skipping missing file: ${yearInfo.file}`); continue; }
    // meetings-YYYY.json is a plain top-level array, not wrapped in a {meetings:[...]} object.
    const data = JSON.parse(fs.readFileSync(file, 'utf8'));
    allMeetings.push(...(Array.isArray(data) ? data : []));
  }
  const pendingMeetings = allMeetings.filter(m => !transcriptMemos[m.id] && Array.isArray(m.segments) && m.segments.length);
  console.log(`Meetings: ${allMeetings.length} total in requested years, ${pendingMeetings.length} need a memo generated\n`);

  console.log('--- Generating transcript memos ---');
  await runPool(
    pendingMeetings.map(m => Object.assign({ __label: `${m.id} (${fmtDate(m.date)})` }, m)),
    async (m) => {
      const sourceText = budgetedText((m.segments || []).join('\n\n'), 9000);
      if (!sourceText) return;
      const memo = await withRetry(
        () => callOllama(buildMemoPrompt({
          dateLabel: m.date ? fmtDate(m.date) : null,
          sourceText,
          sourceDescription: `the transcript of a West Orange Board of Education ${primaryType(m)} meeting`
        })),
        m.__label
      );
      transcriptMemos[m.id] = memo;
      fs.writeFileSync(TRANSCRIPT_MEMOS_PATH, JSON.stringify(transcriptMemos, null, 2));
    },
    CONCURRENCY
  );

  console.log(`\nDone. transcript-memos.json has ${Object.keys(transcriptMemos).length} entries.`);
  console.log('Next: commit transcript-memos.json to the root of wo-schoolboard-ledger-data and push -- the live widget will pick it up automatically.');
}

main().catch(e => { console.error(e); process.exit(1); });
