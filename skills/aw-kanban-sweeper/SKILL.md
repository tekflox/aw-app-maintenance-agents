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
   party that can actually decide. Three narrow, explicitly-granted
   exceptions to this — never a completeness verdict, see each section for
   why: "Not needed" archiving (mootness on hard evidence), the "Priority
   check" (the flow itself already decided a human is needed; the sweeper is
   only completing a move that decision already made but failed to apply),
   and the "SweepBlock" third rule (the sweeper judging its own repeated
   inability to route the work, never the work itself).
2. **A card whose run is not terminal is untouchable.** Running or pending
   means the work may still be happening. Resuming or re-dispatching into
   that is exactly the double-fire this board's claim mechanism exists to
   prevent elsewhere — skip it, every time, no exception.

Not run in a flow when the scheduler fires it — **do not call
`mark_flow_done`** on your own run or on any card you touch. End with a
plain-text report instead.

---

## Plan memory — read first, write last

**Gate: only applies when this run is a Plan iteration.** The cadence-driven
"Kanban Sweep" Plan dispatches you with an input that starts
`# Plan iteration <seq> — ...` and carries a line reading
`` Plan iteration id: `<id>` `` (`agents-platform-multitenant`
`backend/app/core/plans.py:538-542`). The legacy standalone scheduled task
(`aw-app.json`'s "Kanban sweep — stale In Progress / Need Human") has no
plan at all and never produces that heading. If your input doesn't start
that way, **skip this whole section** — do not call `get_plan_memory` or
`update_plan_memory`, there is no iteration for them to attach to and a
write from this path would silently skew the ring for every reader.

**READ, once, before Batch 1:**

```
get_plan_memory("kanban-sweep")
```

Pull `ext["kanban-sweeper"].carry` and the full `iterations` ring (bounded
to 10 entries, newest first) — carry-forward notes and recent passes' card
lists feed candidate ordering alongside `LastSweptAt`. `LastSweptAt` stays
the authoritative per-card back-off; memory only supplements it, it never
replaces it or overrides a back-off skip.

**The schema — this is the one file that owns the sweeper's concrete
vocabulary** (the generic `plan-steward` prompt deliberately only teaches
the abstract shape; see `agents-platform-multitenant` `seed.py:176`):

```json
ext["kanban-sweeper"] = {
  "schema_version": 1,
  "outcome_legend": {
    "moved_done": "card moved to Done",
    "archived_not_needed": "card archived as moot, not completeness",
    "resumed": "original session resumed via invoke_kanban_agent",
    "routed": "escalated to product-owner/architect (see item.to)",
    "backed_off": "skipped: LastSweptAt back-off still in effect",
    "skipped_active_run": "skipped: a non-terminal run is in flight",
    "skipped_quota": "candidate passed checks but the 5-card quota was full",
    "resume_failed": "invoke_kanban_agent failed, fell back to run_agent_async",
    "blocked": "sweeper could not act, left for a human",
    "escalated": "third rule fired: N identical refusals, moved to need_human + pinged a human"
  },
  "iterations": [
    {
      "seq": 9,
      "iteration_id": "<Plan iteration id from the prompt>",
      "run_id": "<this run's own AW_RUN_ID>",
      "finished_at": "2026-11-10T23:40:00+00:00",
      "decision": "pushed",
      "counts": {"moved_done": 2, "routed": 3},
      "items": [
        {"id": "<notion page_id>", "title": "<=60 chars", "outcome": "moved_done"},
        {"id": "<notion page_id>", "title": "<=60 chars", "outcome": "routed",
         "to": "architect", "note": "optional, short"}
      ]
    }
  ],
  "carry": {
    "<notion page_id>": {"note": "short reminder for next pass", "expires": "2026-11-15"}
  }
}
```

`outcome_legend` exists so an agent that has never read this skill can
still make sense of the ring from `get_plan_memory` alone — keep it in sync
if you ever add or rename an outcome value. `counts` and `items` are
optional per the generic envelope, but the sweeper always writes them: they
are exactly what answers "which cards moved to Done this iteration" without
parsing prose. `iterations` is newest-first. `PlanIteration.notes` stays the
free-text narrative, unchanged — this ring is the queryable counterpart; do
not dump this JSON into `notes`, and do not drop the narrative either.

**WRITE, once, immediately before `finish_plan_iteration`:**

Build this iteration's entry from the Plan-iteration-prompt fields
(`seq`, `iteration_id`) plus your own run id and what you actually did this
pass, prepend it to the `iterations` you read, keep the `carry` map current
(drop entries past their `expires`), then:

```
update_plan_memory(slug="kanban-sweep", writer_key="kanban-sweeper",
                    value=<the whole subtree above, with your new entry prepended>,
                    summary="<1-3 line rolling gist of the last pass>")
```

`value` REPLACES your whole subtree — this is why you read it first. Keep
at most 10 entries in `iterations`, and drop the oldest ones while the
encoded subtree exceeds roughly 16 KiB (the 64 KiB cap is shared with every
other writer on this document); trim `title` to 60 chars. On a 413, trim
harder (drop more old entries) and retry **once**; if it still refuses,
proceed to `finish_plan_iteration` anyway and say so in `notes` — a lost
memory write must never cost the iteration close.

**`value` must be the JSON object itself, never a JSON-encoded string.**
Pass `value={"schema_version": 1, ...}` as a native object — if you build it
as a string (e.g. `json.dumps(...)`) and pass THAT string as `value`, the
server stores it verbatim, and `ext["kanban-sweeper"]` becomes a string
instead of an object: a reader then has to parse it before reaching
`.iterations[0]`, defeating the whole point of a structured ring. Confirmed
live on iteration 10 (2026-11-11) — the written subtree was exactly this,
a stringified blob, not a nested object.

Then close the iteration as normal. This section changes nothing about how
or when you call `finish_plan_iteration` itself — see the `plan-steward`
agent's own contract for that; this is only about what you write before it.

**Known, accepted race:** `update_plan_memory` replaces the whole subtree
you read, with no version check on your own `writer_key`. Only one Plan
iteration is normally open on "kanban-sweep" at a time (the cadence claim),
so this only bites if a prior iteration's session is still alive and writes
late after the watchdog already timed it out and freed the claim for a new
one — a late write from a dead-but-not-yet-stopped session landing after
the new iteration's write. Writing promptly, right before closing, is the
only mitigation this skill gives you; there is no server-side fence (same
risk the Architect named rather than a new one), and memory is supplementary
to `LastSweptAt`, never authoritative, so a lost or overwritten entry here
degrades the "what did the last run do" answer, not the sweep's own
correctness.

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

## Back-off — LastSweptAt

`LastSweptAt` (a Notion `date` property) records the last time the sweeper
touched this card without its status changing. The sweeper is its **only**
writer, and it always posts its own comment **before** stamping — so "a
comment newer than `LastSweptAt`" means something happened on the card since
the last sweep that the sweeper hasn't accounted for yet.

**The stamp itself is written on a deferred wake-up, not inline in the sweep
pass.** `invoke_kanban_agent` and the `run_agent_async` fallback are
fire-and-dispatch: they return as soon as the dispatch is accepted, before
the resumed/fallback run has actually replied. That reply lands as a
comment too, under the same no-authorship-distinction rule the back-off
check applies to everything else, so a stamp written right after dispatch
is structurally guaranteed to predate the very reply the sweeper itself
triggered, defeating the back-off on the first pass regardless of elapsed
time (confirmed live on card 40b5bf3b-9510-81e5-acfb-f68ad99e86b9,
2026-11-07: sweeper stamp at 04:04Z, resumed session's own reply at 04:06Z,
re-qualified at the very next sweep). The rule and the comparison above are
unchanged, only the timing of the write moved, see "Acting on a qualifying
card" and "Deferred stamp" below.

Use `register_callback`, not `schedule_wakeup`, to arm this. The sweeper's
own primary trigger is the daily scheduled task
(`aw-app.json`: `"Kanban sweep — stale In Progress / Need Human"`), which
dispatches via a plain `POST /api/agents/kanban-sweeper/run` with no
`initiator_kind` override — defaulting to `"agent_run"`
(`agents-platform-multitenant/backend/app/models.py`), never a
telegram/watch device turn. `schedule_wakeup` needs a resolvable delivery
identity to arm at all (`resolve_delivery_identity`,
`core/delivery_identity.py` — "Wakeup, agent_run, mcp and chat turns never
write" the `cli_sessions` anchor, and the Run walk-back only matches
telegram/watch); on a scheduled sweep pass there is none, so it would be
refused outright every single time, never persisting a wake-up at all.
`register_callback` has no such requirement — it resumes the origin
session directly, agent-to-agent, and the backend's own comment on this
is explicit: "`channel` only decides how a human gets notified — it must
NOT gate whether the caller's session gets resumed" (`wakeups.py`,
`_rerun_and_deliver`). That makes it the only one of the two that actually
works on this agent's own primary trigger.

It is not free of trade-offs either: the watch behind it gives up after a
hard ~1h wall-clock ceiling (`_CALLBACK_WATCH_TIMEOUT_S`, same file), past
which it notifies sysadmins over Telegram and never wakes the sweeper for
that card — `LastSweptAt` stays unset, and the card is exactly as exposed
to a repeat touch as it was before this fix, for that one card only. A
resumed/fallback turn told explicitly not to redo or extend the work
should resolve in minutes, so this is a rare-outlier risk, not the common
path, and it is strictly a reversion to the PRE-fix behavior for that one
slow card rather than a new failure mode — the next scheduled pass simply
re-discovers it with a now-terminal run and the register_callback this
time has every chance to succeed. See "Deferred stamp" below for how the
wake-up itself is handled.

**Back-off rule** — applies to **both** batches, after the staleness filter,
before anything else:

* `LastSweptAt` unset → the candidate proceeds as today.
* `LastSweptAt` set → the candidate qualifies **only if** at least one
  comment on the card has a timestamp strictly after `LastSweptAt`.
  Otherwise **skip**, reason: `"backed off: last swept <ts>, no comment
  since"`.

If nothing was done on the card since the last sweep, there is no reason to
push it again — you would just get the same result. **Any** comment after
`LastSweptAt` re-arms eligibility, no matter how old `LastSweptAt` is, and no
matter who wrote it — human or agent, no authorship distinction.

**How to check:**

* Stamp: `get_kanban_properties(page_id, properties=["LastSweptAt"])` → an
  ISO string, or `null` if never swept (the date flattens to its start
  value, `client.py:389-390`).
* Latest comment: `get_kanban_card(page_id, include_comments=true)` →
  `comments_md` lines read `"- **<created_time>** — text"`, oldest →
  newest; take the last line's timestamp.
* Compare as instants, not strings — the stamp may read `+00:00` while a
  comment timestamp reads `Z`.

**Fail open.** If comments can't be read, a timestamp doesn't parse, or the
thread might be truncated (the comments fetch in
`repos/aw-app-notion/notion_app/sync.py:383-401` pulls a single page of 100
comments with no pagination — on a 100+-comment card the **newest** comments
are exactly the ones missing), treat the card as eligible. One wasted touch
beats a card permanently stranded by a read that can't be trusted. For huge
threads see the `aw-autoskill-huge-kanban-card-comments` skill.

---

## Priority check — escalation that never moved the card

A card whose most-recently-dispatched run set `flow_needs_human=true` (a
durable column on the `Run` row — `core/executor.py::_escalate_need_human`
sets it on every run sharing that run's `flow_run_id` once a flow exhausts
its reprompt and escalates) but whose own Kanban `Status` never actually
reached `"Need Human"` is a **correctness signal, not a staleness one**: the
flow already decided a human is needed, and only the mechanical move failed
(the exact failure class Kanban 4155bf3b-9510-8198-9c1f-f86163100b01 fixed on
the AP-MT side — this check is the backstop for if that path, or some future
equivalent of it, ever breaks the same way again). Run this **before** Batch
1 and Batch 2, every pass, **regardless of any card's `last_edited_time` or
`LastSweptAt`** — a card stuck on a failed escalation could be minutes old
and still needs this now, not at the next 12h-stale pass.

`list_runs` has no server-side filter for `flow_needs_human` — checked the
MCP tool's actual param list (`category`, `limit`, `notion_task_id`,
`session_id`, `status`; no flag for this) rather than assuming it existed.
The field itself IS present on every row the tool already returns (AP-MT's
`RunOut` schema serialises the whole `Run` row), so this is a client-side
scan over an existing call, not a schema change anywhere:

```
list_runs(limit=300)
```

Unfiltered, global, newest-first (`list_runs`' own ordering). Walk it once:

1. Keep the **first row seen per distinct `notion_task_id`** — recency order
   means that is each card's most-recently-dispatched run; a card's older
   runs under it are irrelevant here.
2. From those, keep only rows with `flow_needs_human == true`.
3. From those, keep only rows whose own `status` is terminal
   (`success`/`error`) — same reasoning as Batch 1's run check: a run still
   `running`/`pending` may yet take a terminal flow action on its own before
   anyone needs to intervene, so don't race it.
4. For each survivor, `get_kanban_properties(page_id, properties=["Status"])`.
   Skip if it already reads `"Need Human"` (nothing to fix) or `"Done"` /
   `"Archived"` (superseded by later legitimate action — flagging it now
   would be noise, not a catch). Anything else qualifies.

**Known, stated limitation, not a silent cap:** this only sees a card's
escalation if it falls within the newest 300 runs platform-wide. A card
whose escalated run has scrolled past that window will not be
rediscovered by this check (though it remains reachable by Batch 1/2 if it's
also stale by the normal rule). Report this explicitly if the scan looks
saturated (300 rows returned and the oldest is recent) — that is the signal
the window may be too narrow for current traffic, raise it rather than
silently widening or narrowing `limit` on your own.

**Back-off — applies here too, deliberately, once a card has been picked by
this check at least once.** Frederico left this as a judgment call rather
than a fixed rule: the *first* time a card qualifies, skip the 12h/
`LastSweptAt` gate entirely, per above — but if the sweeper already
corrected this exact card's move on a prior pass and a human still hasn't
acted on it, re-touching it every single day is pure noise with no new
information (the flow's own decision hasn't changed, only the elapsed time
has). So: run the ordinary **Back-off — LastSweptAt** check (see above) on a
candidate here exactly as Batch 1/2 do, and skip on failure with the same
`"backed off: last swept <ts>, no comment since"` reason. This does not
reopen the staleness gate this section exists to bypass — `last_edited_time`
is never consulted here, only `LastSweptAt` + the comment-since check.

**Cap to the fixed-5 budget before acting on anything.** Steps 1-4 plus the
back-off filter can surface more survivors than the budget has room for —
plausible precisely in the outage scenario this check exists to backstop,
where many cards fail to move at once. Sort survivors **oldest-run-first**
by the qualifying row's own `ended_at` (the longest-stuck escalation is the
most urgent one) and take **at most 5**. If more than 5 survive, the rest
are **not** touched this pass — report the overflow count explicitly (they
remain eligible candidates on the very next pass, back-off permitting; this
is a deferral, not a drop). This is the same invariant "Batch size" states
for the sweep as a whole — this check does not get to spend more than the
whole pass's budget on its own, even though it skips the staleness gate.
Whatever it takes from the 5 is what's left for Batch 1/2, per "Batch size"
below.

**Acting on a qualifying card — a second narrow, explicitly-granted
exception to Rule 1** (the first is "Not needed" archiving, below): the
flow itself already made the completeness/correctness call here
(`flow_needs_human=true` is that decision, already recorded) — the sweeper
is not deciding anything new, only finishing a move the flow tried and
failed to make. Unlike Batch 1/2, there is no session worth resuming: the
run that escalated already ended, and re-asking it "is this done?" makes no
sense when it has already answered "a human needs to look at this." So move
the card directly:

```
move_kanban_task(
  page_id,
  status="need_human",
  comment=f"🧹 kanban-sweeper: Agents Flow escalated this to Need Human "
          f"(run {run.id}, agent {run.source_slug}) but the card never "
          f"moved — correcting it now. See the run for the original reason."
)
```

`move_kanban_task` posts the comment before the status patch, same as every
other move in this skill — one call, no separate `add_kanban_comment`.
Then stamp `LastSweptAt` **inline, immediately** (not deferred): there is no
dispatch here to wait on, so none of "Deferred stamp"'s reasoning for
delaying the write applies — stamp it the same way the "blocked, can't
help" comment-only case does.

This check's picks count toward the same **fixed-5 total** as Batch 1/2 (see
"Batch size" below) — it runs first and takes priority, but does not grant
extra budget. Batch 1's quota becomes `5 - len(priority_picked)`, and
Batch 2's becomes `5 - len(priority_picked) - len(batch1_picked)`.

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
3. Walk down that list and, for each candidate, run the **back-off check**
   first, then the **run check** below — in that order, so a card that's
   already backed off never costs a `list_runs` call. Take the **first
   `5 - len(priority_picked)` that pass both** (the Priority check above
   runs first and may have already spent part of the fixed-5 budget).
   Fewer passing than that is fine — do not relax the staleness, back-off,
   or terminal-run rules to fill the quota.

### Back-off check

See "Back-off — LastSweptAt" above. A card that fails it is **skipped**,
reported the same way as a run-check skip — reason `"backed off: last swept
<ts>, no comment since"`.

### Run check (non-negotiable exclusion)

For each candidate card, two lookups — a cheap liveness probe, then the
pinned one:

```
list_runs(notion_task_id=page_id, limit=1)                          # 1. liveness probe, unfiltered
list_runs(notion_task_id=page_id, category="work", limit=1)         # 2. the pinned lookup
```

`list_runs` forwards both `notion_task_id` and `category` straight to AP-MT's
`GET /api/runs?notion_task_id=...&category=...`. `category` distinguishes a
real agent/workflow dispatch (`"work"`, the default) from a monitor/wrapper
dispatch like `monitor-shell` (`"monitor"`, stamped at dispatch time) — before
this column existed, the sweep (and `invoke_kanban_agent`'s own internal
lookup) could resume a wrapper row that happened to carry a `session_id`,
sending "is this done?" to the wrapper's own empty conversation instead of
the agent that actually did the work (Kanban
4115bf3b-9510-8156-9e5e-f6c772ad2538). Property reads, board scans, and fuzzy
matching on the card itself are all dead ground — proved unreliable
escalating this exact gap — so none of them gate the skip/qualify decision
below.

**Deploy-order dependency.** `category` only has an effect once BOTH
agents-platform-multitenant (the `Run.category` column + the `GET
/api/runs?category=...` filter) and the MCP `list_runs` tool
(aw-app-agents-platform-runners) have shipped. Until then the param is
silently ignored server-side — call ② degenerates to exactly call ①'s
unfiltered behavior, i.e. today's pre-fix state, not a new failure mode. If
this app is released/reinstalled before those two land, the sweeper is not
worse off than it is today; it just isn't protected yet. Sequence
deploys so those two ship first, then release this app — otherwise this
section describes a protection that reads as live but silently isn't.

1. **Liveness probe (unfiltered, call ①):** if the newest row for this card
   — of *any* category — has `status` `running` or `pending`, **skip, never
   touch this card.** This preserves today's conservatism even when the
   live row is a monitor/wrapper dispatch (e.g. a still-running
   `monitor-shell` watch) rather than the real agent session — a card is not
   touched while anything tied to it is still in flight, regardless of which
   dispatch category that is.
2. **The pinned lookup (`category="work"`, call ②)** — only reached once
   call ① confirmed nothing non-terminal is in flight. Pre-existing Run rows
   from before this column existed are backfilled to `"work"` by the
   migration itself (a DDL-level `ALTER TABLE ... DEFAULT 'work'`, not an
   ORM-only default) — they never read as null/unset, so no older real
   dispatch is excluded here.
   * **No row returned** → **skip, reason: "no dispatched run found."** A
     card whose only Run rows are monitor/wrapper dispatches correctly reads
     as having no dispatched work, not as resumable via the wrapper. Record
     this as wall `no-work-run` via the SweepBlock Update rule (see
     "SweepBlock — repeat-wall detection and escalation" below).
   * **A row is returned** — it is, by construction, the single most recent
     **real** (non-monitor) run dispatched against this card. Check its
     `status`:
     * `success` or `error` → **terminal, qualifies.** Carry the row itself
       (`id`, `status`, `ended_at`, `source_slug`, `session_id`) into the
       next step — no separate `get_run` call needed.
     * `running` or `pending` (or anything else non-terminal) → **skip,
       never touch this card.**

`AgentRunId` (`get_kanban_properties(page_id, properties=["AgentRunId"])`) is
a **shortcut hint only**, and only when it holds a canonical 32-char hex run
id — use it to sanity-check the row call ② returned (same id = expected; a
mismatch is not by itself a reason to distrust `list_runs`, which reads the
real table that `AgentRunId` is just a property mirror of). Never decide off
`AgentRunId` alone, and never fall back to it when `list_runs` returns
nothing — that would reintroduce the exact unreliable path this replaces.

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

`<N>` = hours since that run's own finish time (the `ended_at` on the row the
run check already carried forward), not since `last_edited_time`.

* **`invoke_kanban_agent` succeeds** (resumed a real session) → that is the
  action, and its JSON result carries a `run_id` for the dispatched turn.
  Keep the `(page_id, run_id)` pair — this card's `Status` property at the
  time of touch is `"In Progress"` by construction (the raw Notion option
  label for Batch 1's `running` filter, the same value `get_kanban_card`
  will hand back later), so there is nothing extra to capture for
  "Deferred stamp" below. The resumed session's own eventual outcome is
  not yours to wait for or report as final — see "What to report" below.
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
  what it already claimed to have done, not the full transcript. This
  response carries its own `run_id` too, keep it the same way.

  If this fallback **also** fails, record wall `no-session` via the
  SweepBlock Update rule below, comment what was tried, and move on — there
  is no third dispatch path to try.
* **`invoke_kanban_agent` returns the server's QA-dedup guard 409** (a QA
  run already exists for this card; the guard also fires on a resume, not
  only a fresh dispatch) → do **not** try the `run_agent_async` fallback,
  it hits the identical guard — record wall `qa-dedup-409` via the
  SweepBlock Update rule below and comment, same as today's existing
  decline-and-comment behavior.
* **`invoke_kanban_agent` or its fallback dies with the zero-token/no-LLM-
  turn transport-failure signature** (see the
  `aw-autoskill-kanban-sweeper-resume-transport-failures` skill for the
  retry/fallback protocol) → once that protocol is exhausted with no
  successful dispatch, record wall `resume-transport` via the SweepBlock
  Update rule below and comment.

Then, either way:

```
add_kanban_comment(page_id, text="🧹 kanban-sweeper: <what was done and why>")
register_callback(run_id=<the run_id from whichever call above fired>)
```

Comment first, register second, never stamp `LastSweptAt` here directly.
Keep `(page_id, run_id, "In Progress")` in your own notes — you need the
mapping back from `run_id` once the wake-up arrives, see "Deferred stamp"
below. If `register_callback` itself fails after the comment already
posted, report it and move on, the card just stays eligible next run,
the safe direction, same as a failed stamp write used to be.

The comment is both the audit trail and the cooldown — it resets
`last_edited_time` so this card won't be picked again for 12h even if
nothing else changes; the stamp (whenever it lands) is the separate, new
back-off clock on top of it.

---

## Batch 2 — Need Human complement (fills remaining slots up to 5)

Only runs if the Priority check + Batch 1 together picked fewer than 5.
Needed = `5 - len(priority_picked) - len(batch1_picked)`.

```
list_kanban_cards(status="need_human", limit=100, order="edited")
```

Same staleness filter (`last_edited_time` ≥12h ago), same newest-first walk.
Walk candidates until `needed` of them are **picked** — a candidate is not
picked until it passes the back-off check below; failing it does not count
against `needed`. **No run-check here** — a Need Human card by definition
has nobody actively working it, so there is no in-flight run to collide
with.

For each candidate:

```
get_kanban_properties(page_id, properties=["LastSweptAt"])
get_kanban_card(page_id, include_comments=true)
```

The `get_kanban_card` call does double duty: its `comments_md`'s last line
is both the back-off comparison (see "Back-off — LastSweptAt" above) and,
once the card passes, the escalation reason — no second read needed. Run
the back-off check first; a candidate that fails it is **skipped**, reported
the same way as in Batch 1, and the card is left untouched.

Once a candidate passes back-off, it is picked. Read **only the most recent
comment** — that is the escalation reason. Some of these cards carry 150K+
chars of comment history; do not read the whole thing, slice the tail. (See
the `aw-autoskill-huge-kanban-card-comments` skill if the response needs that
treatment.)

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
once it has decided, not you. Keep the response's `run_id`, same as in
Batch 1.

Then, same as Batch 1:

```
add_kanban_comment(page_id, text="🧹 kanban-sweeper: <what was done and why>")
register_callback(run_id=<the run_id just returned>)
```

Same ordering as Batch 1 — comment first, register second, never stamp
inline here. Keep `(page_id, run_id, "Need Human")` in your own notes,
same as Batch 1. See "Deferred stamp" right below for how the wake-up
this produces gets handled.

---

## Deferred stamp — closing the loop on wake-up

`register_callback(run_id=...)` re-invokes THIS session once that run
goes terminal, carrying its result. Expect to be woken up this way once
per card you touched in Batch 1 or Batch 2 (not for a card that was
skipped, backed off, or archived — those never produced a run_id or a
registration), each at its own unpredictable time, not all together and
not necessarily in the order you touched the cards.

Each wake-up only carries the one `run_id` it's about — you are not
juggling a shared list across turns. Session-resume for a given
`session_id` goes through a lease, not a strictly-ordered FIFO queue, and
that lease fails open to unserialized execution under a Redis outage or
if the wait itself times out (`_acquire_session_lock` /
`_SESSION_LOCK_MAX_WAIT_S`, `executor.py`) — so two wake-ups landing at
once is possible in principle, just rare. It is harmless here regardless:
each wake-up re-reads the ONE card it's about fresh from Notion rather
than trusting any shared in-memory state, so even a concurrent delivery
can't corrupt another card's handling, at worst it is a redundant,
independent check.

On each such wake-up:

1. Match the `run_id` in the wake-up to the `(page_id, run_id,
   status_at_touch)` you recorded for it while dispatching. `status_at_touch`
   is the raw Notion `Status` option label the card had when you touched
   it — `"In Progress"` for a Batch 1 card, `"Need Human"` for a Batch 2
   one (`running` → `"In Progress"`, `need_human` → `"Need Human"` per
   `repos/aw-app-notion/notion_app/kanban/config.py`'s `DEFAULT_STATUSES`
   — the exact string `get_kanban_card`/`get_kanban_properties` hand back
   for that property, not the logical filter key).
2. Check the card's CURRENT `Status` (`get_kanban_properties(page_id,
   properties=["Status"])` or `get_kanban_card`). If it no longer equals
   `status_at_touch` (the resumed/dispatched work moved it, e.g. to
   `"Done"` or `"Archived"`, or a Batch 2 card out of `"Need Human"`),
   the card already dropped out of the back-off's scope — **skip the
   stamp entirely**, same reasoning as "Not needed" below. Also clear
   `SweepBlock` here if it held a value for this card (per the Update rule
   in "SweepBlock — repeat-wall detection and escalation" below) — a status
   change means whatever it was tracking no longer applies.
3. Otherwise, check whether this touch was actually **effective** before
   stamping — this is the one SweepBlock wall that can only be known here,
   never during the sweep pass itself. The wake-up already carries the
   dispatched run's final `status` and `session_id`; for "a new reply
   landed," read the card's comments (`get_kanban_card(page_id,
   include_comments=true)`) and check whether the latest one postdates the
   "🧹 kanban-sweeper: ..." comment you posted when you dispatched this
   touch.
   * **Terminal `error`, or terminal `success` with no new reply comment**
     → record wall `resume-ineffective:<session8>` (`<session8>` = the
     first 8 characters of the dispatched run's own `session_id`) via the
     SweepBlock Update rule below.
   * **Terminal `success` with a new reply comment** → a clean touch;
     clear `SweepBlock` per the Update rule, if it held a value for this
     card already.
4. Regardless of step 3's outcome, stamp now — SweepBlock is a separate,
   additional write, never a substitute for the `LastSweptAt` back-off
   clock:

   ```
   set_kanban_property(page_id, "LastSweptAt", <now>)
   ```

   `<now>` is the current UTC time as full ISO-8601 **with** the time
   component (e.g. `"2026-11-06T04:03:17+00:00"`), never date-only —
   `LastSeenAt`'s date-only shape is exactly the trap that makes it
   useless for this, and `build_property_payload` passes the string
   straight into `{"date": {"start": ...}}` (`client.py:368-369`), so
   whatever you write is exactly what the next sweep's comparison gets.
   Compute it fresh, immediately before this call, for this card only,
   never reuse a value from an earlier card's wake-up or from the sweep
   pass itself. `set_kanban_property` has no server-side notion of "now";
   a shared timestamp reused across cards defeats back-off outright, the
   same failure mode a shared batch-start timestamp caused before this
   fix (Kanban 4115bf3b-9510-812f-b755-da9ce5b65046 — 6 previously-swept
   cards found sharing one of only two timestamps across groups of 3),
   just moved to a new place to get it wrong.

   If `set_kanban_property` fails here, report it and move on, the card
   stays eligible next run, the safe direction.

No separate report is needed for this wake-up turn beyond a short note of
what you did, this is bookkeeping closing out a card from a previous
pass, not a new sweep.

A "blocked, can't help" comment-only touch with no dispatch at all has
nothing to register a callback on, so for that case alone, stamp
`set_kanban_property(page_id, "LastSweptAt", <now>)` inline, right after
the comment, exactly as before, there is no reply to wait for.

---

## Not needed — archiving a moot card

Rule 1 says you never decide whether work is complete. This is a narrow,
explicitly-granted exception to it, not a reversal: archiving here is a
**mootness** call, not a completeness verdict. You are not saying the work
is done; you are saying the work no longer needs doing.

The sweeper may move a card out of the sweep with:

```
move_kanban_task(status="archived", comment="🧹 kanban-sweeper: not needed — <evidence>")
```

**Only** on narrow, checkable evidence:

* (a) the card's work targets a known-retired/deactivated repo — today that
  is `agentic-workspace` (see the `agentic-workspace-repo-is-fully-deactivated`
  KB entry; check the knowledge base at sweep time for the current list, do
  not treat this as fixed), or
* (b) the work verifiably already shipped elsewhere — name the commit or
  card in the comment.

Any doubt → the normal path, not archive.

This maps to the live Notion option **"Archived"** (the default logical→
option map in `repos/aw-app-notion/notion_app/kanban/config.py:22`, confirmed
live on the data source). `move_card` posts the comment before the status
patch when one is given (`cards.py:376-405`), so pass the evidence as the
`comment` argument, not a separate `add_kanban_comment` call.

**No `LastSweptAt` stamp on this path** — status changed, so the ordinary
"touched without a status change" stamping rule doesn't apply.

**Do not use the board's literal "Not Needed" option** — it has no logical
key in the status map, so a card moved there disappears from
`list_kanban_cards`'s status enum and the sweeper (and everything else)
loses track of it entirely. Frederico chose `archived` specifically to
avoid that.

---

## SweepBlock — repeat-wall detection and escalation (third exception to Rule 1)

The two rules at the top make the sweeper correctly refuse to force work
through a wall it hits — but on their own they give it no way to notice "I
have hit this *exact* wall on this *exact* card three sweeps running" and do
anything but comment and move on, forever. This section is that mechanism:
a dedicated Notion property tracking repeat walls per card, a closed
vocabulary of what counts as a "wall," and a fixed escalation once a wall
repeats three times. Like the "Priority check" above, this is the sweeper
judging its own repeated inability to **route** the work — never a
completeness verdict on the work itself, so it does not reopen Rule 1.

### The property

`SweepBlock` is a Notion `rich_text` property, sweeper-only writer, same
precedent as `LastSweptAt` — a dedicated property, not comment-parsing
(explicitly rejected by Frederico for the same reason `LastSweptAt` exists).
One line:

```
<wall_key>|<count>|<last_attempt_iso8601>|<detail>
```

e.g. `qa-dedup-409|2|2026-11-10T04:05:17+00:00|run eae68794`. `<detail>` is
free text giving the one piece of context that matters for that `wall_key`
(a run id, a session-id prefix) — empty string if the wall_key carries none.
Read/write it exactly like `LastSweptAt`: `get_kanban_properties(page_id,
properties=["SweepBlock"])` to read, `set_kanban_property(page_id,
"SweepBlock", <value>)` to write — `rich_text` already round-trips
end-to-end (`repos/aw-app-notion/notion_app/kanban/client.py:359-360` write,
`:377-378` read), no app change needed.

**One-time provisioning, not automatic:** `SweepBlock` must exist as a
`rich_text` property on the board's Notion database before any of this can
do anything — the same manual one-time schema add `LastSweptAt` needed.
Confirm it round-trips with `get_kanban_properties(page_id,
properties=["SweepBlock"])` on a scratch card before relying on it; until
that property exists, every write here fails and this whole section is a
no-op.

A `SweepBlock` value that fails to parse (wrong shape, unknown `wall_key`)
is treated as **absent** — proceed as if this is the wall's first
occurrence (count starts at 1). Never abort the card or the pass over a
malformed stamp.

### Wall taxonomy — closed vocabulary

Exactly these five `wall_key` values. Nothing else is a "wall" for this
mechanism — an ordinary skip (staleness, back-off, quota) is not a wall and
never touches `SweepBlock`.

| `wall_key` | Fires when |
|---|---|
| `qa-dedup-409` | `invoke_kanban_agent` (Batch 1, "Acting on a qualifying card") returns the server's QA-dedup guard 409 — a QA run already exists for this card, and the guard also fires on a resume, not only on a fresh dispatch. |
| `no-work-run` | The run check's call ② (`list_runs(notion_task_id=page_id, category="work", limit=1)`, Batch 1) returns no row. |
| `no-session` | `invoke_kanban_agent` fails explainably (most commonly: no `session_id` on the pinned run) **and** the `run_agent_async` fallback also fails. |
| `resume-transport` | `invoke_kanban_agent` or its fallback dies with the zero-token/no-LLM-turn transport-failure signature (see the `aw-autoskill-kanban-sweeper-resume-transport-failures` skill) and the retry/fallback protocol there is exhausted with no successful dispatch. |
| `resume-ineffective:<session8>` | At the deferred wake-up ("Deferred stamp" above), the dispatched run (resume, fallback, or a Batch 2 route) is terminal with `error`, or terminal with `success` but left no new comment on the card. `<detail>` = the first 8 characters of that run's own `session_id`. |

"Identical wall" = same card **and** same `wall_key` (and, for
`resume-ineffective`, the same `<session8>` suffix — a different session is
a different wall, not a repeat of this one).

### Update rule — every pass, on every blocked/failed touch

Whenever a touch anywhere in this skill (Priority check, Batch 1, Batch 2,
or a Deferred-stamp wake-up) ends in one of the five outcomes above instead
of a clean resolution, read the current `SweepBlock` (the same
`get_kanban_properties` call already reading `LastSweptAt`/`Status` for
that step costs nothing extra to extend) and:

* **Same `wall_key` (+ same `<detail>` where one applies) as the stored
  value** → increment the count, refresh the timestamp.
* **Different `wall_key`, or no prior value, or a value that fails to
  parse** → overwrite with `count=1`.
* **The touch resolves cleanly** — dispatch accepted and, for anything with
  a registered callback, the wake-up sees a terminal `success` **with** a
  new reply comment — **or** the card's `Status` changed since the last
  stamp (same check "Deferred stamp" already does for `LastSweptAt`) →
  **clear** `SweepBlock` (write empty string). A clean touch or a status
  change both mean whatever this property was tracking no longer applies.

`resume-ineffective` is the one wall that can only be known at the
wake-up — it is written there, in the same turn as the deferred
`LastSweptAt` stamp, never during the sweep pass itself (the sweep pass
only knows the dispatch was *accepted*, not whether it was *effective*).
Every other wall is known immediately, inline, during the sweep pass, and
is written right alongside that step's existing comment.

### The third rule — escalate at count = 3 (fixed, not configurable)

When an increment brings a `wall_key`'s count to **3**:

**a) Always**, regardless of `wall_key`:

1. `move_kanban_task(page_id, status="need_human", comment=...)` — the
   comment must name the `wall_key`, the three attempt timestamps, every
   run id involved, and (for `qa-dedup-409` specifically) the existing QA
   run's id and its verdict. State explicitly: "escalated by the sweeper's
   third rule after 3 identical refusals." If the move is rejected (the
   hard-lock while `QAStatus="In Progress"` — see "Known edges" below),
   fall back to `add_kanban_comment` with the same content and report the
   rejected move; do not retry the move or skip the rest of this sequence.
2. `ask_human(question=...)` with the same evidence, phrased as the
   concrete decision a human actually needs to make (not just "look at
   this card").
3. Write `SweepBlock` as `<wall_key>|escalated|<iso>|<detail>`. An
   `escalated` marker never re-escalates on its own — it clears only via
   the Update rule's status-change path above, same as any other value.

**b) Additionally**, only for platform-shaped walls — a bug in the platform
itself, not something only Frederico can decide — file or bump a dedup'd
finding card via `create_kanban_task` (dedup by `finding_key` gives
platform-wide occurrence counting for free):

| `wall_key` | `finding_key` | `agent_slug` |
|---|---|---|
| `resume-transport` | `reliability:invoke-kanban-agent-resume-transport` | `debugger` |
| `resume-ineffective` where the resumed run's `source_slug` ≠ the card's own `AgentSlug` | `reliability:invoke-kanban-agent-wrong-session` | `debugger` |
| `qa-dedup-409` (only if still hit after the sibling run-id-selector card ships) | `reliability:kanban-sweeper-qa-dedup-wall` | `debugger` |

`no-work-run` and `no-session` get **no** finding card — these are human
triage calls (a genuinely missing dispatch, or a genuinely sessionless run),
not a platform-defect pattern a debugger can act on from a finding card
alone.

This is the **third** narrow, explicitly-granted exception to Rule 1 (see
"Priority check" above for the second, and "Not needed" for the first): the
sweeper is not judging whether the work is complete, it is judging its own
repeated inability to *route* that work to whoever can decide — squarely
its own lane, same reasoning as the Priority check.

**Known edges:**

* The `need_human` move is hard-locked while the card's `QAStatus` is "In
  Progress" (the AP-MT move API) — on rejection, fall back to comment +
  `ask_human` only, still stamp `escalated`, and report the rejected move
  explicitly.
* If `ask_human` fails on a scheduled (`agent_run`-initiated) session, fall
  back to comment-only and report it; do not touch any verdict already
  written.
* A deliberately long-running epic whose resumed session replies "keep In
  Progress" is a **clean** touch — the wake-up sees a new reply comment, so
  it clears `SweepBlock`, never escalates. (This is exactly the 40b5bf3b
  epic's shape — see "Deferred stamp" above.)
* The ~1h `register_callback` ceiling means some wake-ups never arrive; a
  missed wake-up just delays `resume-ineffective` counting for that one
  card — the safe direction, not a correctness gap.

Escalation picks still count toward the fixed-5 budget like everything else
— see "Batch size" below; the third rule spends no extra budget of its own,
it only changes what happens to a card already picked this pass (or, for
`resume-ineffective`, already picked on a *previous* pass and now being
closed out at its wake-up).

---

## Batch size

**Fixed at 5 total** (Priority check + Batch 1 + Batch 2 combined). Not
configurable, not a default — do not read it from anywhere else or let it
drift with board size. The Priority check runs first and takes from this
same budget; it does not add to it. Escalation picks (the SweepBlock third
rule above) count toward this same budget like everything else — they do
not add to it either.

---

## What to report

End with a plain-text report, not a presentation and not `mark_flow_done`:

* Cards **considered** in each batch, and the staleness/ordering evidence
  (`last_edited_time` for each).
* Cards **picked**, in order, with why they were chosen over the others.
* Cards **skipped**, and why — non-terminal run, lease-token/malformed
  `AgentRunId`, run lookup failed, or simply past the quota.
* Cards skipped as **backed off**, with the `LastSweptAt` value that caused
  the skip.
* Cards **archived as not-needed**, with the evidence cited for each.
* Per picked card: what action was taken (resumed / fallback-dispatched /
  routed to product-owner vs architect and why), and the **observed outcome
  so far** — if the resumed/dispatched session hasn't concluded by the time
  you report, say that plainly. Its eventual verdict is its own, not yours
  to wait for or fabricate.
* Per picked card, say that its `LastSweptAt` stamp is **deferred** via
  `register_callback`, not written in this pass — it lands (or is
  correctly skipped, if the card's status changed first) on a later
  wake-up, see "Deferred stamp". Do not report a card as "stamped" in
  this pass; it isn't yet. This does not apply to a Priority-check pick —
  that stamp is written inline, in this same pass, say so plainly.
* Cards **picked by the Priority check**, separately from Batch 1/2 —
  which run's `flow_needs_human` triggered it, the run's own status, and the
  card's Status before the correcting move. If the scan window
  (`list_runs(limit=300)`) came back saturated, say so — that's the signal
  it may be missing older escalations, not proof there are none.
* Every **SweepBlock increment** this pass, whether or not that card was
  otherwise picked — `"wall qa-dedup-409 now 2/3 on card <page_id>"` — this
  includes `resume-ineffective` increments written at a wake-up, which are
  bookkeeping for a card picked on a *previous* pass, not this one.
* Cards **escalated** by the SweepBlock third rule — the `wall_key`, the
  three attempt timestamps, every run id cited, whether the `need_human`
  move succeeded or fell back to comment-only, and whether a platform
  finding card was filed or bumped (and its `finding_key` if so).

Report what is true, not what is tidy — a skipped batch, a run check that
failed to resolve, or a session still in flight all belong in the report
exactly as they happened.
