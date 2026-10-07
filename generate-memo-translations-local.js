#!/usr/bin/env node
/**
 * Batch-translates an existing English memo file (meeting-memos.json, resolution-memos.json, or
 * transcript-memos.json -- any file shaped { "<id>": "<memo text>", ... }) into Spanish, using a
 * LOCAL model via Ollama -- no API key, no cloud call. Same toolchain as generate-memos-local.js
 * and generate-transcript-memos-local.js, reused here for translation instead of summarization.
 *
 * This produces the -es.json file the live widgets check for before falling back to on-demand
 * translation (see ensureMeetingMemoEs / ensureResolutionMemoEs / ensureTranscriptMemoEs in the
 * widget code) -- one script, works for all four memo files across both ledger sites, since the
 * header-set choice is the only thing that differs between them.
 *
 * SETUP: same Ollama setup used for the other batch scripts (ollama serve + a pulled model).
 *
 * WHERE TO RUN THIS: from the root of the data repo clone that has the input file
 * (wo-ledger-data for meeting-memos.json/resolution-memos.json, wo-schoolboard-ledger-data for
 * meeting-memos.json/transcript-memos.json).
 *
 * Usage:
 *   node generate-memo-translations-local.js <input.json> <output-es.json> <headerSet> [model]
 *
 *   headerSet is "meeting" (8 sections: Title & Date, Attendees, Purpose & Objectives, Key
 *   Discussion Points, Decisions Made, Action Items, Unresolved Issues, Next Steps -- used for
 *   meeting-memos.json on both sites and transcript-memos.json on School Board) or "resolution"
 *   (5 sections: Record & Date, Summary, Key Provisions, Effective Date, Related References --
 *   Town Ledger's resolution-memos.json only).
 *
 * Examples:
 *   node generate-memo-translations-local.js meeting-memos.json meeting-memos-es.json meeting
 *   node generate-memo-translations-local.js resolution-memos.json resolution-memos-es.json resolution
 *   node generate-memo-translations-local.js transcript-memos.json transcript-memos-es.json meeting
 *
 * Requires Node 18+ (built-in fetch, no npm install). Resumable: writes the output file
 * incrementally, skips any id already present in it on the next run.
 */

const fs = require('fs');
const path = require('path');

const OLLAMA_URL = process.env.OLLAMA_URL || 'http://localhost:11434';
const INPUT_ARG = process.argv[2];
const OUTPUT_ARG = process.argv[3];
const HEADER_SET_ARG = process.argv[4];
const MODEL = process.argv[5] || 'llama3.1:8b';
const CONCURRENCY = 1;
const RETRY_ATTEMPTS = 2;
const RETRY_DELAY_MS = 3000;
const REQUEST_TIMEOUT_MS = 120000; // translation is a much shorter generation than a full memo

const HEADER_SETS = {
  meeting: {
    en: ['Title & Date', 'Attendees', 'Purpose & Objectives', 'Key Discussion Points', 'Decisions Made', 'Action Items', 'Unresolved Issues', 'Next Steps'],
    es: ['Título y Fecha', 'Asistentes', 'Propósito y Objetivos', 'Puntos Clave de Discusión', 'Decisiones Tomadas', 'Tareas Pendientes', 'Asuntos Pendientes', 'Próximos Pasos']
  },
  resolution: {
    en: ['Record & Date', 'Summary', 'Key Provisions', 'Effective Date', 'Related References'],
    es: ['Registro y Fecha', 'Resumen', 'Disposiciones Clave', 'Fecha de Vigencia', 'Referencias Relacionadas']
  }
};

if (!INPUT_ARG || !OUTPUT_ARG || !HEADER_SETS[HEADER_SET_ARG]) {
  console.error('Usage: node generate-memo-translations-local.js <input.json> <output-es.json> <meeting|resolution> [model]');
  process.exit(1);
}

const INPUT_PATH = path.resolve(INPUT_ARG);
const OUTPUT_PATH = path.resolve(OUTPUT_ARG);
const HEADERS_EN = HEADER_SETS[HEADER_SET_ARG].en;
const HEADERS_ES = HEADER_SETS[HEADER_SET_ARG].es;

// ---- Exact same prompt as the live widget's buildTranslationPrompt -- kept byte-for-byte
// identical so a pre-generated translation reads the same as one generated live. ----
function buildTranslationPrompt(englishMemo) {
  const headerPairs = HEADERS_EN.map((h, i) => `${h} -> ${HEADERS_ES[i]}`).join('\n');
  return `Translate the following memo into Spanish for a West Orange, NJ resident. Keep the exact same structure: the same sections, in the same order, each header alone on its own line.
Replace each English header with its Spanish equivalent exactly as shown here:
${headerPairs}
Rules:
- Translate naturally into Spanish. Do not translate proper nouns, people's names, street addresses, or resolution/ordinance numbers.
- Preserve the action-items bullet structure exactly: "- tarea -- responsable -- fecha límite" (task, then owner, then deadline, separated by " -- ").
- If a section says "None noted in the record", translate it as "No se indica en el registro".
- Do not add commentary, notes, or explanation before or after the memo. Output only the translated memo.
- Do not use markdown formatting anywhere -- no bold, no headings, no numbered lists.
MEMO TO TRANSLATE:
${englishMemo}`;
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
          { role: 'user', content: 'Produce the translated memo.' }
        ],
        stream: false
      })
    }, REQUEST_TIMEOUT_MS);
  } catch (e) {
    if (e.name === 'AbortError') throw new Error(`Timed out after ${REQUEST_TIMEOUT_MS / 1000}s.`);
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
        console.error(`FAILED (giving up after retries): ${item.id} -- ${e.message}`);
      }
      completed++;
      const secs = ((Date.now() - start) / 1000).toFixed(1);
      console.log(`  [${completed}/${items.length}] ${item.id} (${secs}s)`);
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
  console.log(`Input:  ${INPUT_PATH}`);
  console.log(`Output: ${OUTPUT_PATH}`);
  console.log(`Header set: ${HEADER_SET_ARG}`);
  console.log(`Model: ${MODEL}`);

  const reachable = await checkOllamaReachable();
  if (!reachable) {
    console.error(`\nCan't reach Ollama at ${OLLAMA_URL}.`);
    console.error('  ollama serve');
    console.error(`  ollama pull ${MODEL}`);
    process.exit(1);
  }

  if (!fs.existsSync(INPUT_PATH)) {
    console.error(`Can't find ${INPUT_PATH}.`);
    process.exit(1);
  }
  const englishMemos = JSON.parse(fs.readFileSync(INPUT_PATH, 'utf8'));
  const spanishMemos = loadJsonSafe(OUTPUT_PATH);
  console.log(`English entries: ${Object.keys(englishMemos).length}`);
  console.log(`Already translated: ${Object.keys(spanishMemos).length}`);

  const pending = Object.keys(englishMemos)
    .filter(id => !spanishMemos[id] && englishMemos[id])
    .map(id => ({ id, en: englishMemos[id] }));
  console.log(`Remaining to translate: ${pending.length}\n`);

  console.log('--- Translating memos to Spanish ---');
  await runPool(
    pending,
    async (item) => {
      const es = await withRetry(() => callOllama(buildTranslationPrompt(item.en)), item.id);
      spanishMemos[item.id] = es;
      fs.writeFileSync(OUTPUT_PATH, JSON.stringify(spanishMemos, null, 2));
    },
    CONCURRENCY
  );

  console.log(`\nDone. ${path.basename(OUTPUT_PATH)} has ${Object.keys(spanishMemos).length} entries.`);
  console.log(`Next: commit ${path.basename(OUTPUT_PATH)} to the root of this data repo and push -- the live widget will pick it up automatically.`);
}

main().catch(e => { console.error(e); process.exit(1); });
