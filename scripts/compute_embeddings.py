"""
Embed every segment of the document corpus (documents-*.json, per documents-index.json's
"document_categories") using Cloudflare Workers AI's @cf/baai/bge-small-en-v1.5 model, and write
ONE combined embeddings-all.json covering every category.

Originally this wrote one embeddings-<slug>.json per category, and the worker fetched each of
them separately. That worked at 8 categories but broke at 9 -- "Too many subrequests by single
Worker invocation" (Cloudflare's hard cap on outbound fetch()/binding calls per request). Every
additional category was one more mandatory fetch on every single question, on top of the
document-text fetches for whichever categories won and the meeting-transcript fetches -- it was
always going to hit a wall as more categories got added, just a question of when. Consolidating
to one file fixes this permanently regardless of how many categories exist: it's always exactly
one fetch to retrieve every embedding, no matter whether there are 9 categories or 30.

No Vectorize, no external embedding API bill of any real size: bge-small-en-v1.5 costs 1841
neurons per M input tokens, and Workers AI gives 10,000 free neurons/day on every plan
(Free or Paid). The whole ~2.7M-token document corpus comes out to roughly 4,900 neurons --
under half of one day's free allocation, in a single run.

Does NOT touch meetings-YYYY.json -- meeting transcripts stay on the existing keyword-overlap
retrieval path in worker.js.

Usage:
    export CLOUDFLARE_ACCOUNT_ID=...
    export CLOUDFLARE_API_TOKEN=...       # needs "Workers AI" read/write permission
    python3 scripts/compute_embeddings.py .

    # test the pipeline without calling the API:
    python3 scripts/compute_embeddings.py . --stub

Output: embeddings-all.json, shape:
    {"vectors": [{"id": "<docId>--<segmentIndex>", "v": [384 floats], "docId": ...,
                  "segmentIndex": ..., "slug": ..., "category": ..., "subcategory": ...,
                  "title": ..., "date": ..., "docType": ..., "sourceUrl": ...]}
No segment text is stored here -- worker.js fetches the matching documents-<slug>.json (using
the "slug" field to know which one) to pull the actual text for the handful of winning
candidates, which keeps this file -- and the amount of JSON a Worker has to parse on every
request -- much smaller than the full document corpus.

Also updates documents-index.json with a top-level "embeddings_file" key (replaces any older
per-category "embeddings_file" entries from a previous version of this script).
"""
import os, re, sys, json, math, time, hashlib, argparse, urllib.request, urllib.error

MODEL = "@cf/baai/bge-small-en-v1.5"
DIMENSIONS = 384
BATCH_SIZE = 90  # Workers AI batch text endpoint; keep comfortably under per-call payload limits
MAX_EMBED_INPUT_WORDS = 400  # segments are ~350 words already; this is a safety cap under the
                              # model's 512-token max input


def l2_normalize(vec):
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [round(x / norm, 6) for x in vec]


def stub_embedding(text, dims=DIMENSIONS):
    """Deterministic fake embedding for pipeline testing -- NOT for real search quality."""
    h = hashlib.sha256(text.encode()).digest()
    vec = [((h[i % len(h)] / 255.0) * 2 - 1) for i in range(dims)]
    return l2_normalize(vec)


def call_workers_ai_batch(account_id, api_token, texts):
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{MODEL}"
    body = {"text": texts}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {api_token}"},
        method="POST",
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            if not data.get("success", True) and data.get("errors"):
                raise RuntimeError(f"Workers AI error: {data['errors']}")
            vectors = data["result"]["data"]
            return [l2_normalize(v) for v in vectors]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Workers AI {e.code}: {e.read().decode()}")
    raise RuntimeError("Workers AI: exhausted retries")


def load_segments(out_dir):
    with open(os.path.join(out_dir, "documents-index.json")) as f:
        index = json.load(f)
    categories = index.get("document_categories", {})
    if not categories:
        print("No document_categories in documents-index.json -- run parse_documents.py and "
              "build_documents_index.py first.", file=sys.stderr)
        sys.exit(1)

    segments = []
    for slug, info in categories.items():
        with open(os.path.join(out_dir, info["file"])) as f:
            docs = json.load(f)["documents"]
        for doc in docs:
            for i, seg_text in enumerate(doc["segments"]):
                embed_text = " ".join(seg_text.split()[:MAX_EMBED_INPUT_WORDS])
                segments.append({
                    "embed_text": embed_text,
                    "metadata": {
                        "id": f"{doc['id']}--{i}",
                        "docId": doc["id"],
                        "segmentIndex": i,
                        "slug": slug,
                        "category": doc["category"],
                        "subcategory": doc.get("subcategory") or "",
                        "title": doc["title"],
                        "date": doc.get("date") or "",
                        "docType": doc.get("doc_type") or "",
                        "sourceUrl": doc.get("source_url") or "",
                    },
                })
    return segments, index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--stub", action="store_true", help="skip the API, use fake vectors")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = ap.parse_args()

    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not args.stub and not (account_id and api_token):
        print("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set (and --stub not given). "
              "Export both or pass --stub to test the pipeline with fake vectors.",
              file=sys.stderr)
        sys.exit(1)
    if not args.stub and not re.fullmatch(r"[0-9a-f]{32}", account_id or ""):
        print(f"CLOUDFLARE_ACCOUNT_ID doesn't look like a real Cloudflare account id "
              f"(got: {account_id!r}) -- it should be a 32-character hex string from your "
              f"Cloudflare dashboard, not a placeholder. Fix the export and re-run.",
              file=sys.stderr)
        sys.exit(1)

    segments, index = load_segments(args.data_dir)
    total_words = sum(len(s["embed_text"].split()) for s in segments)
    total_tokens_est = total_words * 1.3
    print(f"{len(segments)} segments across the document corpus (~{total_words:,} words, "
          f"~{total_tokens_est:,.0f} tokens)")
    if not args.stub:
        est_neurons = (total_tokens_est / 1_000_000) * 1841
        print(f"Estimated Workers AI cost: ~{est_neurons:,.0f} neurons "
              f"(free allocation is 10,000/day) -- effectively $0 in a single run")

    out_path = os.path.join(args.data_dir, "embeddings-all.json")
    vectors_out = []
    for i in range(0, len(segments), args.batch_size):
        batch = segments[i:i + args.batch_size]
        texts = [s["embed_text"] for s in batch]
        if args.stub:
            vecs = [stub_embedding(t) for t in texts]
        else:
            vecs = call_workers_ai_batch(account_id, api_token, texts)
        for seg, v in zip(batch, vecs):
            vectors_out.append({**seg["metadata"], "v": v})
        print(f"  embedded {min(i + args.batch_size, len(segments))}/{len(segments)}",
              end="\r", flush=True)
    print()
    with open(out_path, "w") as f:
        json.dump({"vectors": vectors_out}, f)
    print(f"wrote {len(vectors_out)} vectors -> {out_path}")

    # Clean up any older per-category embeddings_file keys from a previous version of this
    # script, and set the single combined one instead.
    for slug, info in index.get("document_categories", {}).items():
        info.pop("embeddings_file", None)
    index["embeddings_file"] = "embeddings-all.json"
    with open(os.path.join(args.data_dir, "documents-index.json"), "w") as f:
        json.dump(index, f, indent=2)
    print("documents-index.json updated: top-level embeddings_file = embeddings-all.json")


if __name__ == "__main__":
    main()
