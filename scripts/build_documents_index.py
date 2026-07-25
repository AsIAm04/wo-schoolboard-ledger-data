"""
Merge documents-categories.json (produced by parse_documents.py) into documents-index.json,
adding a "document_categories" key alongside the existing "years" (meeting transcripts) key.
Safe to re-run -- only that key is replaced, "years"/"total_meetings" are left untouched.

Usage:
    python3 scripts/build_documents_index.py .
"""
import json, os, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "."

with open(os.path.join(OUT, "documents-categories.json")) as f:
    cats = json.load(f)

index_path = os.path.join(OUT, "documents-index.json")
with open(index_path) as f:
    index = json.load(f)

index["document_categories"] = cats["categories"]
index["total_documents"] = cats["total_documents"]

with open(index_path, "w") as f:
    json.dump(index, f, indent=2)

print(f"documents-index.json now has {len(index.get('years', {}))} meeting years "
      f"and {len(cats['categories'])} document categories "
      f"({cats['total_documents']} documents total)")
