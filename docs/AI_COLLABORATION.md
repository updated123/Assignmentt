# AI collaboration note

This project was built in two passes with two different AI coding assistants, and
the second one's main contribution was finding that the first pass had been
grading its own homework.

An optional **OpenAI-compatible Chat Completions** call is a *runtime* component
of the product — the judge — not an author of this repository. It is disabled by
default and no published number depends on it.

## Tools and the role of each

| Tool | Pass | Role |
|---|---|---|
| **Cursor Grok 4.6** (in Cursor) | v1.0, the five-day build | Drafted the architecture, application code, YAML scorecard, 12 synthetic packets, the first evaluation harness, and the first documentation set, under human direction |
| **Claude Opus 5** (in Claude Code) | v1.1, hardening | Audited the v1.0 result, found and fixed the rigged baseline and the flattering pass rule, found and fixed six production defects, wrote the 216-test suite and CI |
| Python 3.12 / FastAPI / Pydantic / pypdf / httpx | — | Runtime dependencies, not AI |
| OpenAI-compatible API (optional) | — | Runtime packet judge; **not required** to run or evaluate the system |
| A deterministic no-policy scorer | — | Stands in for ungrounded skim-and-paste in the evaluation (`packet_review_os/baseline.py`) |

## Work delegated to AI

**Pass 1 (build).** Repository layout, pydantic schemas, the extractive scorer,
the action policy, the web UI and CLI, the synthetic Northloop/Maya persona and
its 12 packets, the first evaluation set, and first drafts of all documentation.

**Pass 2 (hardening).** An adversarial review of the working system with two
questions: *is the evaluation honest?* and *what breaks in production?* Then the
fixes, the test suite, and the documentation rewrite around the corrected
results.

What the second pass produced, concretely:

- Rewrote the evaluation to report three outcomes (`exact` / `alternate` / `fail`)
  instead of a single pass/fail against a wide allowed set
- Replaced the rigged baseline with a fair one and retracted the old number
- Added an SSRF guard, recursive PII redaction, an upsert that preserves human
  approvals, WAL storage with atomic transactions, validated configuration with a
  `check` command, upload caps, threadpool offload for the async endpoint, and
  optional token access
- Wrote 216 tests (93% statement coverage) and a CI workflow that gates on lint,
  configuration validity, coverage, and the evaluation itself
- Rewrote README, ARCHITECTURE, EVALUATION, RUNBOOK and CASE_STUDY to match the
  corrected results

## How AI-generated results were verified

- **The evaluation is the primary check, and it was itself put under test.**
  `tests/test_evaluation.py` tests the pass rule, asserts no gold case allows more
  than two actions, and asserts the baseline must win some cases — because a
  baseline that never wins is evidence of rigging, not of quality.
- **Predict, then run.** Each packet was read and its correct action predicted
  *before* trusting the engine, especially TC05 (referral), TC11 (conduct) and
  TC04 (wrong discipline). Disagreements were treated as bugs to investigate, not
  as narrative to smooth over.
- **Grounding is enforced in code, not requested in a prompt.**
  `quote_in_source` checks that every quote is a verbatim substring of the packet;
  anything else is dropped and the score pulled back.
- **The model path is tested without a model.** Six fixtures drive the real
  request/parse/retry/fallback code through a stub HTTP transport, including a
  hallucinated quote, malformed JSON, an HTTP 500, a timeout, and a model that
  tries to advance a conduct knockout.
- **Every AI-proposed fix had to come with a failing-then-passing test.** Two
  bugs were found *by* tests written to check something else: the partial-model-
  reply bug that erased grounded evidence, and the profile-URL redaction gap
  caused by quote windowing stripping the `https://` prefix.
- **The generated sample PDF was checked byte-wise**, not just "does it open" —
  its hard-coded xref offsets were wrong and pypdf was silently repairing them on
  every read.

## Results that were rejected or manually corrected

1. **Retracted the headline result.** v1.0 reported "system 12/12, baseline 1/12."
   Both halves were wrong: the 12/12 hid a case that returned an unexpected
   action, and the 1/12 came from an opponent that had been built to fail. The
   corrected numbers — 11/12 exact against 3/12 — are weaker and true. This was
   the single most important correction in the project.
2. **Rejected "always call an LLM."** The default path is extractive plus policy,
   so the system runs at $0 with no key and the evaluation is reproducible.
3. **Rejected auto-send and ATS write-back.** The first product instinct was more
   agentic; the requirement is that human judgment is retained. `send_allowed` is
   `False` in every path and no mail library is installed.
4. **Rejected letting the model choose the action.** It proposes scores and
   quotes only. LX06 exists to prove the boundary holds.
5. **Corrected the referral policy.** The first version advanced a CEO-referred
   VP. That is a process failure, not a model failure.
6. **Corrected negated keyword matching** (`frontend` inside "no frontend").
7. **Rejected `eval` as a package name** — it shadows the builtin. The harness
   lives in `evaluation/`.
8. **Rejected fabricated adoption metrics.** There is no production telemetry.
   The two-week numbers are labelled as a plan, and the stopwatch timings are
   labelled self-measured, n=3.
9. **Rejected silently relabelling TC02** to match what the system does. Moving
   the gold label to fit the output is the exact thing the three-outcome rule
   exists to prevent. It is reported as an alternate with the disagreement
   explained and the YAML lever named.
10. **Rejected suppressing the pypdf warnings** on the sample PDF in favour of
    fixing the PDF. Muting a warning teaches the reader to ignore warnings.

## Core decisions owned by the human

- **The problem.** Recruiting packet review for a specific proxy user with a
  recurring weekly load — not a generic "AI for HR" tool.
- **The success definition.** Zero false advances on knockout and wrong-role
  cases, every score grounded in a quote, three-step setup for a non-developer.
- **The central architectural constraint.** Policy in code; the model is
  optional and advisory. Everything else follows from this.
- **Synthetic data with a stated proxy-user assumption**, rather than implying
  access to real applicants.
- **Local-first privacy.** SQLite, redaction, no third-party call by default.
- **Scope discipline.** No ATS write-back, no email sending, no OCR, no fairness
  claims — each an explicit non-goal rather than an unfinished feature.
- **The decision to publish the retraction** instead of quietly shipping better
  numbers. An evaluation that cannot report bad news is not an evaluation.

## Honest note on the division of labour

Most of the code and prose in this repository was drafted by AI assistants. What
was not delegated: choosing the problem, defining what "better" means, deciding
that the model must not choose the action, and deciding which AI-proposed results
to throw away. The most consequential human act in the project was refusing to
accept a 12/12 that felt good.
