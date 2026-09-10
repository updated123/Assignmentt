# Architecture — Packet Review OS

## The one idea

**The model proposes; code decides.** Scores and quotes may come from a language
model. The *action* — advance, hold, ask back, reject, escalate — is always
chosen by readable Python in `policy.py` against thresholds in a YAML file. That
is what makes the system evaluable, arguable in a hiring meeting, and safe to
run without a model at all.

`LX06` in the evaluation is the mechanical proof: when the model recommends
advancing a candidate whose packet carries a conduct knockout, the answer is
still `reject_with_reason`.

## End-to-end data flow

```
     paste / PDF upload / job URL          (web form, CLI, or JSON API)
                  │
                  ▼
     ┌────────────────────────────┐
     │ assemble                   │  packet = paste + PDF text + notes + JD
     │  pdf_extract.py  (pypdf)   │  bounded: 8 MB upload, 12 pages
     │  jd_fetch.py     (httpx)   │  SSRF-guarded, 2 MB cap, redirects re-checked
     └────────────────────────────┘
                  │
                  ▼
     ┌────────────────────────────┐
     │ guard                      │  < 80 chars  → exception, ask for more
     │  pipeline.py               │  > 40k chars → truncate + warn
     └────────────────────────────┘
                  │
                  ▼
     ┌────────────────────────────┐
     │ score, extractively        │  every score points at a quotable span
     │  evidence.py               │  positive vs negative indicators
     └────────────────────────────┘
                  │
                  ├── optional ──▶ ┌──────────────────────────────┐
                  │                │ llm.py  (JSON, temp 0.1)     │
                  │                │  retry: transient only       │
                  │                │  quote not in packet → drop  │
                  │                │  score ≥ 2 unquoted → cap 1  │
                  │                │  failure → extractive        │
                  │                └──────────────────────────────┘
                  ▼
     ┌────────────────────────────┐
     │ decide, in code            │  knockouts → referrals → must-have gates
     │  policy.py                 │  → risk flags → thresholds
     └────────────────────────────┘
                  │
                  ▼
     ┌────────────────────────────┐
     │ draft                      │  send_allowed = False, always
     │  drafts.py                 │  no mail transport exists in this project
     └────────────────────────────┘
                  │
                  ▼
     ┌────────────────────────────┐
     │ persist + approve          │  SQLite (WAL), contact details redacted
     │  storage.py / privacy.py   │  human approve / send back = audit event
     └────────────────────────────┘
```

## Module boundaries

| Module | Owns | Does not |
|---|---|---|
| `config.py` | Settings from env, validated YAML models | Know about packets |
| `textutil.py` | Normalisation, quote windows, PII patterns | Make decisions |
| `evidence.py` | Extractive scoring, knockout/flag detection | Choose an action |
| `llm.py` | Model transport, retries, grounding enforcement | Choose an action |
| `baseline.py` | Comparison baselines for evaluation only | Run in the product path |
| `policy.py` | The action decision and confidence | Touch I/O |
| `drafts.py` | Email drafts | Send anything |
| `pipeline.py` | Orchestration and the audit trail | Contain business rules |
| `storage.py` / `privacy.py` | Persistence, redaction, migration | Format output |
| `web.py` / `__main__.py` | Interfaces, access control, input limits | Score or decide |
| `net_guard.py` | Whether a URL may be fetched | Fetch |

## Input / output contracts

**Input — `PacketInput`**

| Field | Type | Notes |
|---|---|---|
| `role_id` | string | Must match `config/roles/{id}.yaml`; the filename is the identity |
| `packet_text` | string | 80–40,000 characters after merge |
| `recruiter_notes` | string | Optional; scanned for referral language |
| `candidate_name` | string | Guessed from the packet if blank |
| `job_url` | string | Optional http(s), SSRF-checked |
| `source` | paste / pdf / cli / eval | Telemetry only |
| `run_mode` | system / baseline | `baseline` is for evaluation |

**Output — `ReviewResult`** (full JSON via `python -m packet_review_os review X --json`)

Per-criterion scores 0–3 with verbatim quotes, contrary evidence and rationale;
knockouts; risk flags; missing fields with answerable ask-back questions;
interview probes; next action + why; confidence + label; draft email with
`send_allowed: false`; warnings; exceptions; engine mode; latency; packet
fingerprint; `policy_overrode_model`.

**Config contracts** are pydantic models (`AppConfig`, `RoleConfig`), so a
scorecard mistake produces a message naming the file and field rather than a
crash on the first real packet. `python -m packet_review_os check` runs them all.

## Choices and trade-offs

| Choice | Rationale | Trade-off accepted |
|---|---|---|
| Policy in code, not in the prompt | Referrals and knockouts must be deterministic and testable | Policy is blunt; no nuance the code lacks |
| Extractive scorer always on | Runs offline, $0, reproducible evaluation | Weaker prose judgment than a large model; misses synonyms |
| Optional LLM as *proposer* | Better rationale where a key exists, without ceding control | Two code paths to test; cost; packet text leaves the machine |
| YAML scorecards | Operators change criteria without deploying Python | Indicator lists over-match; mitigated by quotes and negatives |
| FastAPI + one server-rendered form | A non-developer can use it; no Node toolchain | Not a hosted multi-tenant product |
| SQLite with WAL | Zero ops, local audit trail, survives concurrent CLI + web | Not a shared ATS; single machine |
| Loopback bind by default | Candidate data is not exposed by accident | Needs `API_TOKEN` before any LAN use |
| Human approval mandatory | The recruiter of record stays human | One extra click; no "agent autonomy" story |
| Quote must be a verbatim substring | Kills the failure mode that matters most | Rejects legitimate paraphrase |

## Failure behaviour

| Failure | Behaviour |
|---|---|
| No API key | Grounded extractive engine; stated in the UI and in `engine_mode` |
| Model 5xx / 429 / timeout | Bounded retry, then extractive fallback; review completes |
| Model 401 / 400 | Fatal, no retry (retrying cannot fix a bad key); extractive fallback |
| Model invents a quote | Quote dropped, score pulled back, warning shown to the reviewer |
| Malformed model JSON | Extractive scores kept untouched |
| Image-only PDF | Explicit exception asking for pasted text — never a silent zero |
| Packet too short | `request_missing_info` exception, not a fabricated reject |
| Job URL private/redirecting to private | Refused; the review continues without the JD and warns |
| Broken scorecard YAML | Server refuses to start; CLI exits 2 with the file and field |
| Concurrent writers | WAL + 5 s busy timeout; approval is an immediate transaction |

## Privacy and permissions

- Secrets live only in `.env` (gitignored). No secret is read from repo YAML.
- Packet text never leaves the machine **unless** an operator sets an API key.
- `privacy.py` redacts emails, phone numbers and profile URLs from every
  free-text field of a stored review — including evidence quotes, the draft body
  and the log lines. Candidate **names are deliberately retained**: they are the
  subject of the review, and removing them would void the audit log.
- Loopback by default. Binding elsewhere without `API_TOKEN` logs a warning at
  startup and is called out by `check`; with a token, every route except
  `/health` requires it.
- Residual risk, documented not fixed: the SSRF guard validates the resolved
  address and then requests by hostname, so DNS rebinding is not defeated. See
  `net_guard.py`.

## Integrations

1. **pypdf** — resume PDF text extraction (bounded to 12 pages, 8 MB)
2. **httpx** — optional job-description page fetch (SSRF-guarded, 2 MB, 3 redirects)
3. **OpenAI-compatible Chat Completions** — optional judge, any compatible endpoint

## Extending it

Add a role by copying `config/roles/fullstack_engineer.yaml` to
`config/roles/<new_id>.yaml`, setting `role_id: <new_id>` to match the filename,
and running `python -m packet_review_os check`. No Python changes. The mismatch
between filename and `role_id` is a validated error precisely because copying a
file and forgetting to edit it is the likely mistake.
