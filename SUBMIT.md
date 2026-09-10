# How to attach the five submission slots

Everything except the GitHub URL and the recording already exists in this folder.
Files under `submit/` are **generated** from `docs/` — run
`python scripts/build_submission.py` after any documentation change (CI fails if
they drift).

| Slot | What to attach | Where it is |
|---|---|---|
| **1. Working System** | GitHub URL | Push this folder, then paste `https://github.com/<you>/packet-review-os` |
| **2. Evaluation Package** | File | `submit/EVALUATION_PACKAGE.md` (the evaluation doc plus the latest generated results) |
| **3. Case Study** | File | `submit/CASE_STUDY.md` |
| **4. AI Collaboration Note** | File | `submit/AI_COLLABORATION.md` |
| **5. Demo Video** | Loom URL | Record 5:00 following `docs/DEMO_SCRIPT.md` |

Optional extras (they unlock after the five): `submit/RUNBOOK.md`,
`submit/ARCHITECTURE.md`.

## Before you attach anything

```bat
python -m packet_review_os check          :: configuration is valid
pytest -q                                 :: 216 tests pass
python -m packet_review_os eval            :: regenerates evaluation/results/
python scripts/build_submission.py         :: refreshes submit/ from docs/
```

Regenerating the evaluation before you export means the numbers in
`EVALUATION_PACKAGE.md` are the ones a reviewer will reproduce.

## Two things only you can do

1. **Create the GitHub repo and push.** The repository is committed locally and
   ready; it needs a remote.
2. **Record the five-minute demo.** `docs/DEMO_SCRIPT.md` has the beats, the
   timings and the spoken lines, including the retraction beat at 4:00.
