# Regenerating the data

```
cd wo-schoolboard-ledger-data
python3 scripts/parse.py ~/Documents/"WO school board" ./scripts/_intermediate
python3 scripts/build_data_repo.py ./scripts/_intermediate/all_records.json .
```

This re-scans the six `20XX Board of Education Meeting(s)` folders, re-parses every `.txt`
transcript, and overwrites `meetings-YYYY.json` + `documents-index.json` in the repo root. Review
the diff, then commit and push as usual. `scripts/_intermediate/` is a scratch file, safe to
delete or gitignore.
