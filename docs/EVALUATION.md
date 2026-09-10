# Evaluation package — Packet Review OS

Companions: `evaluation/gold_cases.json`, `evaluation/run_eval.py`,
`evaluation/results/LATEST.md`, `evaluation/results/latest.json`, and the unit
suite in `tests/`.

Reproduce everything with two commands:

```bat
python -m packet_review_os eval
pytest -q
```

The eval command exits non-zero if any case lands outside its allowed action set,
any invariant breaks, or any fixture fails — so review quality is a build gate,
not a number in a document.

---

## 1. What counts as a pass

### Three outcomes, not two

Each gold case carries one `expected_action` (the label a reviewer would defend)
and an `allowed_actions` set of at most two (other actions defensible on the same
packet). Every case reports one of:

| Outcome | Meaning |
|---|---|
| `exact` | Returned the gold action |
| `alternate` | Returned a different action the case explicitly allows — reported separately, never folded into the headline |
| `fail` | Outside the allowed set, or an invariant broke |

**Why this changed.** The v1.0 report headlined **12/12 passed** while TC02
returned `reject_with_reason` against an expected `hold_for_specific_interview`.
That was technically true — `reject` was in the allowed set — but a pass rule
that only asks "is it in the allowed set?" rewards widening the allowed set. The
honest number is now **11/12 exact, 1 alternate**, and the disagreement is
visible in the table. `tests/test_evaluation.py` enforces the new rule, including
a check that no gold case allows more than two actions.

### Invariants, scored independently of the action

A case can return a defensible action and still fail. Applicable checks:

| Check | Applies when |
|---|---|
| `avoided_forbidden_action` | case lists `must_not_action` |
| `detected_required_criteria` | case lists `must_detect_criteria` (score ≥ 2 required) |
| `frontend_gap` | case requires React treated as a gap |
| `knockout_fired` / `no_false_knockout` | case requires or forbids a knockout |
| `risk_flagged` | case requires a specific risk flag |
| `no_hallucinated_quotes` | grounded modes only |
| `scores_are_grounded` | grounded modes only — every criterion scoring ≥ 2 must carry a verbatim quote |

---

## 2. The baseline, and why the old one was withdrawn

The system adds two things on top of "read the packet and form a view":
**grounding** (every score points at a quotable span) and **policy** (must-have
gates, knockouts, referral escalation, ask-back instead of guessing). A useful
baseline is therefore a competent reviewer missing exactly those two things.

**The v1.0 baseline was rigged and its 1/12 result is retracted.** It gave
non-must-have criteria a free score of 2 regardless of evidence, advanced
anything scoring ≥ 0.4, and the harness hard-coded its grounding check to
`False`. It could not win. That proves nothing.

**The current deterministic baseline** (`no_policy_baseline` /
`no_policy_action` in `packet_review_os/baseline.py`):

- scores from the same indicator lists as the real scorer, with no generosity hack;
- decides on the weighted score alone, at the *system's own* thresholds from `config/app.yaml`;
- has no knockouts, no referral rule, no must-have gate, no ask-back;
- records no quotes.

Grounding is **not** scored against it. A single unstructured prompt cannot emit
per-criterion quotes by construction, so claiming a win there would be circular.
It is reported as a capability the baseline lacks.
`tests/test_baseline.py` asserts the baseline uses the same thresholds and gets
the easy packets right — a baseline that never wins is evidence of rigging.

**What this baseline is not.** It is a *stand-in* for ungrounded skim-and-paste,
not a measured run of a hosted chat model. A stronger comparison —
`llm_prompt_baseline`, one unstructured "should we advance this candidate?" call
with no scorecard — ships and is unit-tested against a stub transport, but it
needs an API key, so **it has not been run against a live model**. The headline
below is against the deterministic stand-in and is labelled as such. This is the
main gap in the evaluation; see §7.

---

## 3. Results

Generated 2026-09-10, `grounded_extractive` engine, no API key configured.
Full tables: `evaluation/results/LATEST.md`.

| Metric | System | Baseline (no policy, no grounding) |
|---|---|---|
| Exact match on the gold action | **11/12** | 3/12 |
| Defensible alternate | 1 | 0 |
| Outside the allowed set | **0** | 9 |
| Cases passing every invariant | **12/12** | 3/12 |
| False advances on must-not-advance cases | **0** | 5 |
| Scores backed by a verbatim quote | **100%** (68/68) | not available by construction |
| Latency p50 / p95 | 1 ms / 20 ms | 1 ms / 1 ms |
| Reviews requiring human approval | 12/12 | n/a |
| Drafts marked auto-sendable | **0** | n/a |

The baseline's five false advances are TC02 (no React), TC06 (no Python), TC07
(inflated claims), TC10 (unresolved timezone) and **TC11 (hostile conduct)** —
the last being the one that would actually hurt: it advances a candidate whose
packet contains abusive language, because it has no knockout concept. Its action
histogram is 9 advance / 2 reject / 1 hold: with no policy layer, "looks broadly
fine" collapses into "advance".

### The one alternate, stated plainly

**TC02 (strong backend, React explicitly absent).** Gold label is
`hold_for_specific_interview` — probe the gap. The system returns
`reject_with_reason`, because React is a hard must-have in the scorecard and the
packet contains explicit contrary evidence ("No frontend SPA work"). Both are
defensible; the system is stricter than the gold label.

This is a **configuration disagreement, not a bug**, and the lever is in the
YAML: setting `must_have: false` on `react_frontend` in
`config/roles/fullstack_engineer.yaml` moves this case to `hold`. I left the
scorecard strict because a mid-level full-stack req that cannot be filled by a
backend-only engineer is the more common reality, and because a reviewer reading
the output sees the quote and can override. It is reported as `ALTERNATE` rather
than quietly relabelled.

---

## 4. Test set (12 gold cases)

| ID | Kind | Gold action | Allowed | Why it exists |
|---|---|---|---|---|
| TC01 | representative | advance | advance | Happy path, clean strong packet |
| TC02 | representative | hold | hold, reject | Backend-only; must not advance |
| TC03 | edge | request info | request info | Thin LinkedIn dump, first-role signal |
| TC04 | failure | reject | reject | iOS/Swift only — wrong discipline |
| TC05 | edge | escalate | escalate | VP-level + CEO referral; must not auto-close |
| TC06 | representative | reject | reject, request info | React present, Python explicitly absent |
| TC07 | edge | request info | request info, reject | "10x ninja" language, no artifacts |
| TC08 | edge | escalate | escalate | Thin packet but CEO referral |
| TC09 | representative | advance | advance, hold | Skills pass; tenure risk must stay visible |
| TC10 | edge | hold | hold, request info | Skills good, overlap unresolved |
| TC11 | failure | reject | reject | Conduct knockout must beat strong skills |
| TC12 | representative | advance | advance, hold | Noisy email forward of a strong packet |

## 5. Model-path fixtures (6/6)

These drive the real request, parse, retry and fallback code in
`packet_review_os/llm.py` through a stub HTTP transport — **no API key, no
network** — so the optional model path is covered even though the shipped
configuration has no key.

| ID | Scenario | Asserted behaviour |
|---|---|---|
| LX01 | Well-formed grounded JSON | `engine_mode = llm_judge`, score adopted, quote verified in packet |
| LX02 | Model invents a quote | Quote dropped, score pulled back, warning surfaced |
| LX03 | Unparseable content | Falls back to `llm_fallback_extractive`, review still completes |
| LX04 | HTTP 500 | Retried once, then falls back (2 attempts observed) |
| LX05 | Connection timeout | Falls back without retrying (1 attempt, `LLM_RETRIES=0`) |
| LX06 | Model recommends advancing a conduct knockout | **Policy wins**: `reject_with_reason` |

LX06 is the important one: it is the mechanical proof of the "model proposes,
code decides" claim.

## 6. Exception, integration and security fixtures (7/7)

| ID | Kind | Input | Expected |
|---|---|---|---|
| FX01 | exception | 2-character paste | `request_missing_info` + exception message, not a fake reject |
| FX02 | exception | non-PDF bytes | `PdfExtractError` in operator language |
| FX03 | exception | `not-a-url` | `JobFetchError` |
| FX04 | integration | sample resume PDF | Extracts Python + React text |
| FX05 | integration | real HTTP fetch of a local JD page | Contains FastAPI + React |
| FX06 | security | same loopback URL without the opt-in | **Refused** |
| FX07 | security | `http://169.254.169.254/latest/meta-data/` | **Refused** |

FX06/FX07 exist because v1.0 fetched any URL the operator pasted, including
cloud metadata endpoints and the app's own `/history` page. See
`tests/test_net_guard.py` and `tests/test_jd_fetch.py`, which also cover a public
URL redirecting to a private one.

## 7. Metrics, and what is *not* proven

**Speed.** p50 1 ms, p95 20 ms per review in extractive mode. With a model key
the dominant term is one API call; `LLM_TIMEOUT_SECONDS` bounds it at 45 s and a
failure falls back rather than blocking.

**Cost.** **$0** in the shipped configuration — no API calls. With a key, one
judge call per review; the prompt is capped at 14,000 packet characters
(~4k tokens), so roughly one cent per review at gpt-4o-mini list prices. Not
measured against a live endpoint.

**Human touch.** 100% by design. 12/12 reviews require approval; 0 drafts are
marked sendable. `send_allowed` is `False` in every code path and there is no
mail transport in the dependency list.

**Time saved.** Stopwatch on a proxy user, n=3 packets, self-measured:

| Packet | Manual skim + notes | System + human approval |
|---|---|---|
| TC01 strong | 14 min | ~1.5 min |
| TC11 conduct | 11 min (nearly missed the note) | ~1 min |
| TC12 messy paste | 18 min | ~2 min |

At ~10 packets/week this is the difference between ~2.5 h and under 30 min. **These
timings are self-measured on synthetic packets by the system's author, not
observed on a real user with real packets.** Treat them as indicative.

### Honest gaps

1. **No live-model comparison.** The strongest baseline the brief asks for
   (simple ChatGPT use) is implemented and unit-tested but never run against a
   real endpoint. The headline win is over a deterministic stand-in.
2. **No live-model results at all.** Every reported number is extractive-mode.
   The model path is covered by stub fixtures, not by real completions.
3. **Synthetic packets, one role, English only.** Twelve cases written by the
   author. Indicator-based scoring will miss skills phrased with synonyms absent
   from the YAML — the known failure mode, unfixed.
4. **Timings are self-reported**, n=3, by a proxy rather than a real hiring manager.
5. **No fairness or adverse-impact analysis.** Twelve synthetic packets cannot
   support one, and this system must not be presented as bias-audited.

## 8. Failure cases found and fixed

Each is now a regression test, so the fix is fenced rather than remembered.

1. **Negated keywords scored as skills.** `frontend` matched inside "No frontend
   SPA work". Fix: negative indicators take priority on must-haves.
   Fence: TC02, `tests/test_policy.py`.
2. **Referral + strong VP resume auto-advanced.** Escalation only fired on a low
   overall score. Fix: any founder/board/internal referral escalates.
   Fence: TC05, TC08, `test_referral_escalates_rather_than_auto_closing`.
3. **Conduct note lost behind strong skills.** Fix: hostile knockout
   short-circuits to reject. Fence: TC11, LX06.
4. **A partial model reply erased grounded evidence.** Found by
   `test_non_numeric_score_falls_back_to_extractive`: when the model omitted
   `evidence_quote`, the merge blanked the extractive quote and then capped the
   score for having none. Fix: fall back to the extractive quote.
5. **Stored reviews leaked contact details.** The README promised redaction, but
   only one column was redacted while `result_json` carried the candidate's email
   and phone inside evidence quotes. Fix: `packet_review_os/privacy.py` redacts
   every free-text field before the row is written. Fence: `tests/test_storage.py`.
6. **A human's approval could be silently discarded.** `INSERT OR REPLACE` reset
   `approved` on any re-save. Fix: upsert that preserves the decision.
   Fence: `test_resaving_a_run_does_not_discard_an_approval`.
7. **The sample PDF logged xref repair warnings** on every read because its
   cross-reference offsets were hard-coded and then invalidated by a CRLF
   rewrite. Fix: compute the offsets. Fence: `tests/test_pdf.py`.

## 9. Unit and integration suite

216 tests, 93% statement coverage on `packet_review_os/`, CI-gated at 85%.

| Area | File | Focus |
|---|---|---|
| SSRF policy | `tests/test_net_guard.py` | Private, loopback, metadata, IPv4-mapped, split-horizon DNS |
| Job fetching | `tests/test_jd_fetch.py` | Redirect re-validation, size cap, content type, readable errors |
| Model path | `tests/test_llm.py` | Grounding enforcement, retryable vs fatal, timeout, malformed JSON |
| Baselines | `tests/test_baseline.py` | Fair thresholds, no free points, no policy |
| Policy | `tests/test_policy.py` | Knockout precedence, referral escalation, unproven vs disproven |
| Persistence | `tests/test_storage.py` | Redaction, approval survival, v1 migration, concurrent writers |
| Configuration | `tests/test_config.py` | Readable messages for every scorecard mistake |
| Interface | `tests/test_web.py` | Happy path, bad input, approval loop, token access |
| CLI | `tests/test_cli.py` | Exit codes 0/1/2 |
| PDF | `tests/test_pdf.py` | Text extraction, scans, oversize |
| Harness | `tests/test_evaluation.py` | The pass rule itself, and gold-set hygiene |
