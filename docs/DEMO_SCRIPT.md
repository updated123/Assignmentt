# Demo script — 5:00 recording

Record at 1280×720 or higher. Speak slowly. **Do not skip the retraction beat at
4:00** — it is the strongest thirty seconds in the demo.

## Before you record

```bat
.venv\Scripts\activate
python -m packet_review_os check
python -m packet_review_os eval
pytest -q
python -m packet_review_os serve
```

Have these open in tabs:

1. Terminal (running `serve`)
2. <http://127.0.0.1:8000>
3. `evaluation/results/LATEST.md`
4. `samples/packets/TC11_toxic_conduct.txt`

Leave the terminal visible for the first ten seconds so the reviewer sees a real
local process, and keep the `pytest -q` output (`216 passed`) on screen for a
moment before you switch.

---

### 0:00–0:50 — Problem and baseline

Show `samples/packets/TC11_toxic_conduct.txt`.

> Maya reviews about ten of these a week. The old way is skim, paste into a chat
> tool, type a verdict in Slack. Look at this packet: strong Python, and a
> recruiter note that the candidate berated a coordinator. An ungrounded reviewer
> still reports "strong skills," because nothing forces a different action for a
> conduct note. That is the bottleneck — not writing English, but grounding a
> decision and having a policy.

Switch to `LATEST.md`, point at the baseline row:

> Same twelve packets through a reviewer with no policy and no grounding: nine of
> its twelve answers are "advance." Five of those are candidates it should not
> have advanced, including this one.

---

### 0:50–2:10 — Live happy path

Browser → paste `samples/packets/TC01_strong_match.txt` → **Run scorecard review**.

Point at, in this order:

1. **Advance to screen** — and the *why* line underneath it
2. A must-have row's **verbatim quote** — "this is a span of her actual resume,
   checked in code as a substring. If the model invents a quote, it gets dropped"
3. The confidence label
4. The draft email — **"and it says not sent. There is no mail library in this
   project. That is structural, not a prompt instruction."**

Click **Approve this next action**, add a note.

---

### 2:10–2:50 — Non-developer UX and the human gate

> After `serve`, there are no commands. One form, one page.

Open **History**, show the approved row.

> This is what she opens when the founder asks "why did we advance her?" — the
> decision, the evidence, who approved it, when.

---

### 2:50–4:00 — Failure handling

**Conduct knockout.** Paste TC11 → **Reject with a reason**, point at the
hostile-conduct quote.

> Strong skills did not override it. The knockout short-circuits before scoring
> matters.

**Exception path.** Paste `hi` → run.

> Too short. That is an exception, not a decision. The system asks for more
> rather than judging a stub — the one thing a naive prompt will never do.

**Refuse to fetch an internal URL.** In the Job URL field, paste
`http://127.0.0.1:8000/history` and run with a real packet.

> The job-URL field used to fetch anything. That is a server-side request
> forgery hole — it would happily read cloud metadata or this app's own stored
> reviews. It is refused now, and the review continues without the job
> description instead of failing.

If time is tight, cut the URL beat, not the conduct beat.

---

### 4:00–4:40 — The retraction

Back to `LATEST.md`.

> Here is the part I would want to see if I were reviewing this. The first
> version of this project reported twelve out of twelve, against a baseline that
> scored one out of twelve. Both numbers were wrong. The twelve-out-of-twelve hid
> a case that returned a different action than expected, because "pass" only
> meant "inside the allowed set" — and a rule like that rewards widening the set.
> And the baseline had been built to lose: free points for criteria with no
> evidence, and a grounding check hard-coded to fail.
>
> So I retracted it. The evaluation now reports exact matches separately from
> defensible alternates, the baseline uses the system's own thresholds, and the
> tests assert that the baseline has to win some cases. The honest result is
> eleven out of twelve exact, one alternate, zero outside the allowed set —
> against three out of twelve. Weaker, and true.

Point at the TC02 `ALTERNATE` row.

> That is the one disagreement, shown rather than relabelled. React is a hard
> must-have, so the system rejects; a human might hold and probe. One line of
> YAML changes it.

---

### 4:40–5:00 — Results and the most important limitation

> Zero false advances. Every score backed by a verbatim quote. Every review
> requires approval, and no draft is sendable. Two hundred and sixteen tests,
> ninety-three percent coverage, and the evaluation runs in CI — if review
> quality regresses, the build fails.
>
> The biggest limitation: these numbers come from the extractive engine with no
> API key, on twelve synthetic packets I wrote myself, for one role. The strongest
> baseline the brief asks for — a real chat model on the same packets — is
> implemented and unit-tested, but I have not run it against a live endpoint. That
> is the first thing in the next two weeks.

Stop.

---

## Spoken closing line, if you need one

> Packet Review OS changed Maya's recurring packet review from an ungrounded skim
> into a scorecard decision she can defend with quotes. It was proven on twelve
> labelled cases against a fair baseline — and the most useful thing I did was
> throw away my own first result.
