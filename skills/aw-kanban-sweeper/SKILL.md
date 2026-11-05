---
name: aw-kanban-sweeper
description: Daily sweep that closes out stale In Progress / Need Human Kanban cards — resumes the original agent session to ask "is this actually done?" rather than redoing or judging the work itself. Use whenever the first user message begins with `/aw-kanban-sweeper`, or when the "Kanban sweep — stale In Progress / Need Human" scheduled task fires.
---

# aw-kanban-sweeper — stale-card sweep

You close the gap between "a run finished" and "the card still says In
Progress forever because nobody told it otherwise." You are **not** a
reviewer and **not** a fixer: for every card you touch, you hand the
decision back to whoever already has the context — the original agent
session, or the right executor for an escalation — and you never decide a
verdict yourself.

Two rules that override anything you might infer on your own:

1. **You never decide whether work is complete.** That call belongs to the
   agent that did the work (resumed via `invoke_kanban_agent`) or to a human
   (via `ask_human` / `move_kanban_task(need_human)`, which you only trigger
   on their behalf by resuming that agent — you do not call it yourself on a
   Batch 1 card). You read cards, pick candidates, and hand each one to the
   party that can actually decide.
2. **A card whose run is not terminal is untouchable.** Running or pending
   means the work may still be happening. Resuming or re-dispatching into
   that is exactly the double-fire this board's claim mechanism exists to
   prevent elsewhere — skip it, every time, no exception.

Not run in a flow when the scheduler fires it — **do not call
`mark_flow_done`** on your own run or on any card you touch. End with a
plain-text report instead.

---

## Why last_edited_time, not "entered this status at"

There is no Kanban property recording when a card entered its current
status. `LastSeenAt` is date-only and not it. Staleness here is approximated
by the card's own `last_edited_time` — the same trade-off
`kanban_archive_stale.py` in the monolith already accepted. Any comment or
property write resets that clock, which is a feature, not a bug: your own
comment at the end of a sweep pass arms a natural 12h cooldown before the
same card can be picked again.

---

## Batch 1 — stale In Progress

```
list_kanban_cards(status="running", limit=100, order="edited")
```

1. Keep only cards whose `last_edited_time` is **≥12h ago**.
2. Sort the survivors **newest-first** by `last_edited_time` (closest to
   the 12h line first, not the oldest zombies — that is deliberate: a card
   just past the threshold is the one most likely to still have a live,
   resumable session and a human still paying attention to it).
3. Walk down that list and run the **run check** below on each; take the
   **first 5 that pass**. Fewer than 5 passing is fine — do not relax the
   staleness or terminal-run rules to fill the quota.

### Run check (non-negotiable exclusion)

For each candidate card:

```
get_kanban_properties(page_id, properties=["AgentRunId"])
```

* If the value is **not a 32-character hex run id** (e.g. a lease token like
  `mt:<uuid>` or `sweep:<uuid>` that `claim_card()` left mid-flight, or
  anything else that isn't a run id) — **skip**, note why.
* Otherwise:

  ```
  get_run(run_id=<AgentRunId>)
  ```

  * `status` in `{success, error}` → **terminal, qualifies**.
  * `status` in `{running, pending}` (or anything non-terminal) → **skip,
    never touch this card.**
  * Run not found / lookup error → skip, note why (fail closed, same as the
    board's own claim logic).

### Acting on a qualifying card

```
invoke_kanban_agent(page_id, message="""
Your card <page_id> is still In Progress although your run finished
<N>h ago. Re-read the card and your own final report. If the work you
already did and reported is genuinely complete, call mark_flow_done with
the honest outcome (QA accountability applies: an existing qa-* run
auto-resolves; pass qa_not_needed=true only if the work was verified); if
it is NOT complete, escalate — ask_human and/or
move_kanban_task(need_human) with a comment. Do not redo or extend the
work.
""")
```

`<N>` = hours since that run's own finish time (`get_run`'s timestamp), not
since `last_edited_time`.

* **`invoke_kanban_agent` succeeds** (resumed a real session) → that is the
  action. The resumed session's own eventual outcome is not yours to wait
  for or report as final — see "What to report" below.
* **`invoke_kanban_agent` returns an explainable failure** (most commonly:
  "the latest run for this card has no session_id" — it never opened a
  resumable CLI session) → **fallback**:

  ```
  run_agent_async(
    slug=<run.source_slug>,            # the SAME agent that did the work
    input=f"{page_id}\n\n{short_summary_of_final_output}",
    notion_task_id=page_id,
    call_me_back=false,
  )
  ```

  Get `short_summary_of_final_output` from `get_run_detail` or
  `peek_run_output` on the original run — enough for the fresh run to know
  what it already claimed to have done, not the full transcript.

Then, either way:

```
add_kanban_comment(page_id, text="🧹 kanban-sweeper: <what was done and why>")
```

This is both the audit trail and the cooldown — it resets `last_edited_time`
so this card won't be picked again for 12h even if nothing else changes.

---

## Batch 2 — Need Human complement (fills remaining slots up to 5)

Only runs if Batch 1 picked fewer than 5. Needed = `5 - len(batch1_picked)`.

```
list_kanban_cards(status="need_human", limit=100, order="edited")
```

Same staleness filter (`last_edited_time` ≥12h ago), same newest-first walk,
take up to `needed` cards. **No run-check here** — a Need Human card by
definition has nobody actively working it, so there is no in-flight run to
collide with.

For each picked card:

```
get_kanban_card(page_id, include_comments=true)
```

Read **only the most recent comment** — that is the escalation reason. Some
of these cards carry 150K+ chars of comment history; do not read the whole
thing, slice the tail. (See the `aw-autoskill-huge-kanban-card-comments`
skill if the response needs that treatment.)

Decide **fresh, every time — never hardcoded** what kind of question the
latest comment is raising:

| The comment is asking... | Route to |
|---|---|
| A genuine scope / product / priority call | `product-owner` |
| A design / approach question, or a QA bounce-back | `architect` |

```
run_agent_async(
  slug=<"product-owner" or "architect", per the judgment above>,
  input=f"{page_id}\n\n{escalation_comment}\n\ndecide and route; move the card when you have",
  notion_task_id=page_id,
  call_me_back=false,
)
```

The sweep **leaves the card in Need Human** — the dispatched agent moves it
once it has decided, not you.

Then, same as Batch 1:

```
add_kanban_comment(page_id, text="🧹 kanban-sweeper: <what was done and why>")
```

---

## Batch size

**Fixed at 5 total** (Batch 1 + Batch 2 combined). Not configurable, not a
default — do not read it from anywhere else or let it drift with board size.

---

## What to report

End with a plain-text report, not a presentation and not `mark_flow_done`:

* Cards **considered** in each batch, and the staleness/ordering evidence
  (`last_edited_time` for each).
* Cards **picked**, in order, with why they were chosen over the others.
* Cards **skipped**, and why — non-terminal run, lease-token/malformed
  `AgentRunId`, run lookup failed, or simply past the quota.
* Per picked card: what action was taken (resumed / fallback-dispatched /
  routed to product-owner vs architect and why), and the **observed outcome
  so far** — if the resumed/dispatched session hasn't concluded by the time
  you report, say that plainly. Its eventual verdict is its own, not yours
  to wait for or fabricate.

Report what is true, not what is tidy — a skipped batch, a run check that
failed to resolve, or a session still in flight all belong in the report
exactly as they happened.
