# Evaluation results

Generated: 2026-09-10T18:51:00Z · engine: `grounded_extractive`

## Headline

| Metric | System | Baseline (no policy, no grounding) |
|---|---|---|
| Exact match on the gold action | **11/12** | 3/12 |
| Defensible alternate action | 1 | 0 |
| Outside the allowed set (fail) | **0** | 9 |
| Cases passing every invariant | **12/12** | 3/12 |
| False advances on must-not-advance cases | **0** | 5 |
| Scores backed by a verbatim quote | **100.0%** (68/68) | not available |
| Latency p50 / p95 | 1 ms / 23 ms | 1 ms / 1 ms |

- Baseline false advances: ['TC02_backend_heavy', 'TC06_no_python', 'TC07_inflated_claims', 'TC10_timezone_conflict', 'TC11_toxic_conduct']
- System false advances: none
- Model-path fixtures: 6/6
- Exception & security fixtures: 7/7
- Drafts marked auto-sendable: 0 (every one of 12 reviews requires human approval)

`exact` = matched the gold action. `alternate` = a different action that the case
explicitly allows; it is reported separately so a wide allowed set cannot flatter
the score. `fail` = outside the allowed set, or an invariant broke.

## System cases

| Case | Kind | Outcome | Expected | Actual | Notes |
|---|---|---|---|---|---|
| TC01_strong_match | representative | EXACT | advance_to_screen | advance_to_screen | — |
| TC02_backend_heavy | representative | ALTERNATE | hold_for_specific_interview | reject_with_reason | Defensible alternate: expected hold_for_specific_interview, returned reject_with_reason. |
| TC03_incomplete | edge | EXACT | request_missing_info | request_missing_info | — |
| TC04_wrong_role | failure | EXACT | reject_with_reason | reject_with_reason | — |
| TC05_overqualified_referral | edge | EXACT | escalate_to_hiring_manager | escalate_to_hiring_manager | — |
| TC06_no_python | representative | EXACT | reject_with_reason | reject_with_reason | — |
| TC07_inflated_claims | edge | EXACT | request_missing_info | request_missing_info | — |
| TC08_thin_referral | edge | EXACT | escalate_to_hiring_manager | escalate_to_hiring_manager | — |
| TC09_job_hopper | representative | EXACT | advance_to_screen | advance_to_screen | — |
| TC10_timezone_conflict | edge | EXACT | hold_for_specific_interview | hold_for_specific_interview | — |
| TC11_toxic_conduct | failure | EXACT | reject_with_reason | reject_with_reason | — |
| TC12_messy_paste | representative | EXACT | advance_to_screen | advance_to_screen | — |

## Baseline cases (same packets, no policy layer, no grounding)

| Case | Outcome | Expected | Baseline action |
|---|---|---|---|
| TC01_strong_match | EXACT | advance_to_screen | advance_to_screen |
| TC02_backend_heavy | FAIL | hold_for_specific_interview | advance_to_screen |
| TC03_incomplete | FAIL | request_missing_info | reject_with_reason |
| TC04_wrong_role | FAIL | reject_with_reason | hold_for_specific_interview |
| TC05_overqualified_referral | FAIL | escalate_to_hiring_manager | advance_to_screen |
| TC06_no_python | FAIL | reject_with_reason | advance_to_screen |
| TC07_inflated_claims | FAIL | request_missing_info | advance_to_screen |
| TC08_thin_referral | FAIL | escalate_to_hiring_manager | reject_with_reason |
| TC09_job_hopper | EXACT | advance_to_screen | advance_to_screen |
| TC10_timezone_conflict | FAIL | hold_for_specific_interview | advance_to_screen |
| TC11_toxic_conduct | FAIL | reject_with_reason | advance_to_screen |
| TC12_messy_paste | EXACT | advance_to_screen | advance_to_screen |

## Model-path fixtures (stub transport, no API key needed)

- LX01_valid_json_merged: PASS — engine=llm_judge, python_backend score=3
- LX02_hallucinated_quote_dropped: PASS — Dropped hallucinated quote on python_backend.
- LX03_malformed_json_fallback: PASS — engine=llm_fallback_extractive, calls=2
- LX04_http_500_retried_then_fallback: PASS — attempts=2 (expected 2), engine=llm_fallback_extractive
- LX05_timeout_fallback: PASS — attempts=1 (expected 1), engine=llm_fallback_extractive
- LX06_policy_overrides_model: PASS — action=reject_with_reason, knockouts=['hostile_conduct']

## Exception, integration and security fixtures

- FX01_too_short (exception): PASS — Packet is too short (2 characters). Paste a resume, notes, or upload a PDF.
- FX02_bad_pdf (exception): PASS — Could not read bad.pdf. Upload a text-based PDF or paste the resume.
- FX03_bad_job_url (exception): PASS — Job URL must start with http:// or https://
- FX04_pdf_text (integration): PASS — Priya Nair
5 years of experience as a software engineer
Python FastAPI React TypeScript PostgreSQL pytest AWS
Shipped a B2B SaaS billing das
- FX05_job_url_fetch (integration): PASS — Mid-level Full-Stack Engineer — Northloop Mid-level Full-Stack Engineer Northloop is a 22-person B2B SaaS company. You will ship Python APIs
- FX06_ssrf_loopback_blocked (security): PASS — Loopback job URL refused unless allow_private is set.
- FX07_ssrf_metadata_blocked (security): PASS — Refusing to fetch '169.254.169.254': it points at the private/internal address 169.254.169.254. Paste the job description text instead.
