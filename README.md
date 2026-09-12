# Packet Review OS

A hiring manager pastes a messy candidate packet. In under a second the system
returns **a scorecard review where every score points at a verbatim quote, a
next action chosen by readable policy, and a draft email that is never sent**.

The model proposes. Code decides. It runs with no API key at all.

---

## Whose workflow

**Maya Chen** (proxy user), Head of People at **Northloop**, a 22-person B2B
SaaS company. She reviews **8–15 inbound packets per week** for one mid-level
full-stack role. Packets arrive as forwarded emails, LinkedIn dumps, PDFs and
referral notes. Today she skims, pastes into a chat tool, and types a verdict
into Slack. Rework happens when the founder asks "why?" and she cannot point at
evidence.

> **Stated assumption.** Maya is a synthetic but realistic proxy. Packets are
> synthetic, public-style data — not real applicants. The workflow, time cost and
> failure modes are drawn from common small-company recruiting operations, not
> from any named employer's ATS. Timing figures are self-measured; see
> [docs/EVALUATION.md §7](docs/EVALUATION.md).

## Three-step setup

**1. Install dependencies** (Python 3.11+):

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

**2. Copy the environment template.** You can leave every value as-is — there is
no required API key:

```bat
copy .env.example .env
python -m packet_review_os check
```

macOS/Linux: `cp .env.example .env`

`check` validates the configuration and prints what it found. It should say
`Configuration OK`.

**3. Start the app** and open <http://127.0.0.1:8000>:

```bat
python -m packet_review_os serve
```

Pick `TC01_strong_match` from **Try a bundled example**, click **Load example**,
then **Run scorecard review**. Then approve or send back. Nothing is emailed.
(You can also paste any packet by hand; the examples live in `samples/packets/`.)

## Verify it end to end

```bat
python -m packet_review_os eval    :: 12 gold cases, 6 model-path, 7 fixtures
pytest -q                          :: 216 tests (needs requirements-dev.txt)
```

`eval` writes `evaluation/results/LATEST.md` and exits non-zero if any case lands
outside its allowed action set or any invariant breaks. Review quality is a build
gate, not a claim.

## Commands

```bat
python -m packet_review_os check                     :: validate configuration
python -m packet_review_os serve                     :: web app for the reviewer
python -m packet_review_os roles                     :: list role scorecards
python -m packet_review_os review <file>             :: score one packet
python -m packet_review_os review <file> --json      :: full structured output
python -m packet_review_os eval                      :: run the evaluation set
```

Exit codes: `0` fine, `1` runtime problem, `2` configuration problem.

Examples:

```bat
python -m packet_review_os review samples/packets/TC11_toxic_conduct.txt
python -m packet_review_os review samples/resumes/priya_nair.pdf
python -m packet_review_os review samples/packets/TC01_strong_match.txt --notes "Referred by our CEO."
```

There is also a JSON endpoint, `POST /api/review`, taking the same fields.

## What it does, in one paragraph

The packet is assembled from paste, PDF text, recruiter notes and an optional
job-description URL. A packet that is too short is an **exception**, not a
decision — the system asks for more rather than judging a stub. An extractive
scorer scores each criterion only where it can quote a supporting span of the
candidate's own text. If an API key is configured, a model may revise those
scores, but any quote it supplies that is not a verbatim substring of the packet
is dropped, and an unquoted score above 1 is capped. The **action** — advance,
hold, ask back, reject, escalate — is then chosen by code in `policy.py` against
thresholds in `config/app.yaml`. A draft email is prepared with
`send_allowed: false`. The review is stored locally with contact details
redacted, and waits for a human to approve or send it back.

## What this system will not do

- It will not send email, write to an ATS, or schedule interviews. There is no
  mail library in the dependency list and `send_allowed` is `false` in every path.
- It will not decide anything without a human. Every review requires approval.
- It will not read image-only scanned PDFs — it asks you to paste the text.
- It will not fetch a job URL that resolves to a private, loopback or
  cloud-metadata address.
- It is not a general-purpose chatbot, and not a bias-audited hiring tool. See
  the limitations in [docs/EVALUATION.md §7](docs/EVALUATION.md).

## Results at a glance

Extractive engine, no API key, 12 gold cases
(full tables in [evaluation/results/LATEST.md](evaluation/results/LATEST.md)):

| Metric | System | Baseline (no policy, no grounding) |
|---|---|---|
| Exact match on the gold action | **11/12** | 3/12 |
| Defensible alternate | 1 | 0 |
| Outside the allowed set | **0** | 9 |
| False advances on must-not-advance cases | **0** | 5 |
| Scores backed by a verbatim quote | **100%** (68/68) | not available |
| Latency p50 / p95 | 1 ms / 20 ms | 1 ms / 1 ms |
| Drafts marked auto-sendable | **0** | n/a |

The baseline's worst miss is TC11: it advances a candidate whose packet contains
abusive conduct, because it has no knockout concept. That gap is the reason this
system exists.

**Read [docs/EVALUATION.md](docs/EVALUATION.md) §2 before quoting these numbers.**
An earlier version of this project reported 12/12 against a baseline that had
been rigged to lose; that result is retracted and the reasoning is documented.

## Configuration

| What | Where |
|---|---|
| Secrets, model, port, access token, upload cap | `.env` (never committed) |
| Action thresholds, packet limits, confidence bands | `config/app.yaml` |
| Role scorecard, knockouts, indicators | `config/roles/fullstack_engineer.yaml` |

Add a role by copying the YAML, setting `role_id` to match the new filename, and
running `check`. No Python changes.

## Privacy

Reviews are stored in local SQLite (`data/packet_review.db`). Emails, phone
numbers and profile URLs are redacted from every stored field, including evidence
quotes and log lines. Candidate **names are kept deliberately** — they are the
subject of the review. Packet text never leaves the machine unless you configure
an API key. The app binds to loopback and requires `API_TOKEN` before it will be
safely reachable from a network; see [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Deliverables map

| Submission slot | File / link |
|---|---|
| Working system | this repository (setup above) |
| Evaluation package | [docs/EVALUATION.md](docs/EVALUATION.md) + [evaluation/results/LATEST.md](evaluation/results/LATEST.md) |
| Case study | [docs/CASE_STUDY.md](docs/CASE_STUDY.md) |
| AI collaboration note | [docs/AI_COLLABORATION.md](docs/AI_COLLABORATION.md) |
| Demo | [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) (5-minute recording) |
| Operator runbook | [docs/RUNBOOK.md](docs/RUNBOOK.md) |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |

## Repository layout

```
packet_review_os/     application: config, scoring, policy, model, storage, web, CLI
config/               app.yaml + role scorecards (the operator-editable surface)
evaluation/           gold cases, harness, published results
tests/                216 unit and integration tests
samples/              12 synthetic packets, a sample resume PDF, a sample JD page
scripts/              sample-data generator
docs/                 architecture, evaluation, case study, runbook, demo script
.github/workflows/    CI: lint, config check, tests with coverage floor, eval gate
```
