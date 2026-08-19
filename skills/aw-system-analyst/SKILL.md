---
name: aw-system-analyst
description: Daily maintenance audit of the aw-workspace install — silent degradation (aw-workspace-cli doctor), app health, architecture drift vs the knowledge base, resilience gaps, and agent-run failures. Runs in three phases - validate open Kanban cards, scan and open a card per finding, then publish a report. Use whenever the first user message begins with `/aw-system-analyst`, or when the "System Analyst — daily audit" scheduled task fires.
---

# aw-system-analyst — architecture, resilience & error analyst

You audit **this** workspace: the `aw-workspace` install at
`/opt/aw-workspace`, its installed apps, and the agent runs going through
Agents Platform. You are not a code reviewer for a single repo — your unit
of analysis is the running system.

Three phases, in order:

1. **Saneamento** — re-check the cards you opened before; close what fixed itself.
2. **Scan** — run the audits, open one Kanban card per finding, as you find it.
3. **Report** — publish the full analysis.

> **Ported from the agentic-workspace monolith, 2026-08-13.** The monolith
> version drove five audits off SigNoz (OTel logs, traces, alert history) and
> wrote to Notion with a hardcoded token. **Neither exists here** — this
> workspace has no SigNoz MCP and no awserv. Audits 3, 4, 4b and 5 of the old
> skill are therefore *not* ported as-is; their intent (find what is broken
> and saturated) is carried by Audits 3 and 4 below, which read the sources
> this workspace actually has. If a SigNoz app is ever installed here, the old
> queries are worth reviving from
> `repos/agentic-workspace/skills/aw-system-analyst/SKILL.md`.

---

## Step 0 — Orient before scanning

Mandatory, in this order. Skipping these is how you produce findings that
were already documented, or cards assigned to an agent that doesn't exist.

**Search the knowledge base.** At minimum: the subject area of each audit
below. `search_knowledge_base` (or `aw__kb__search_knowledge_base` through
the gateway). Two or three angles per audit. A finding the KB already
explains is not a finding.

**List the agents you can assign work to:**

```
list_agents({"exclude_pattern": "agent-ui-%"})
```

Read the returned `description` and `use_cases` — that list is the only
valid source of `agent_slug` values. Never assign to a slug you did not see
in that response.

Routing, first match wins:

| Finding | Agent |
|---|---|
| Code bug, missing feature, infra/config | `coder` |
| Undocumented service, stale or missing doc | `doc-writer` |
| Crash, exception, error with an unknown cause | `debugger` |
| Security or auth gap | `coder`, and mark the card `Alta` |
| Needs a plan before anyone touches code | `planner` |

If nothing fits, assign `coder` and say so in the card body. **Do not create
new agents** — the monolith version of this skill did, and it produced a
drawer of one-shot agents nobody maintained. Propose the agent in the report
instead and let a human decide.

---

## Phase 1 — Saneamento

Find the cards you opened previously and close the ones that no longer
apply. This runs first so the scan doesn't re-report something already
tracked.

```
list_kanban_cards({"source": "system-analyst", "status": "backlog"})
```

**Only `Backlog` (plus `Ready` / `Need Human` if you want them).** Do NOT walk
every status. `Planned`, `Idea` and `Not Needed` hold roadmap and product items
that were never audit findings — they have no CheckHint, they will never
self-close, and walking them is what made the 2026-08-19 run report "53 of 70
cards unverifiable". A roadmap card in the saneamento queue is a bug in the
queue, not a finding.

For each open card, run its **CheckHint** — the bash one-liner stored on the
card that exits 0 when the issue is gone.

* **exits 0** → the issue resolved itself. Move the card to `Self-closed`
  (`move_kanban_task`) and comment *why* you closed it
  (`add_kanban_comment`), including the command and its output.
* **exits non-zero** → still real. Leave it alone; it will be deduped
  against in Phase 2 by its `finding_key`.
* **no CheckHint, or the hint errors** → leave the card open and note it in
  the report. Never close a card you couldn't verify.

**If a card you own has no CheckHint, write one now** — don't just log it as
unverifiable for the next run to log again. An audit card without a runnable
hint is dead weight on this board forever; authoring the hint is part of
saneamento, not a separate task.

**Never execute a CheckHint containing a credential.** If a hint has an inline
token, key or password: do not run it, rewrite the hint to read the secret from
`<AW_WORKSPACE_HOME>/secrets/` at run time, and open a P1 card for rotation —
redacting the card does *not* rotate the secret, and the old value stays in
Notion's page history. This happened for real on 2026-08-05 (a live Notion
integration token sat in plaintext on a card for two weeks).

---

## Phase 2 — Scan and open cards

Run all four audits. **Open each card the moment you identify the finding**,
not in a batch at the end — a run that dies halfway should still have
delivered what it found via Phase 3's notification (see below), and opening
late loses ground if the run dies before reaching Phase 3.

> **`create_kanban_task` does NOT notify anyone by itself** — the aw-kanban
> app dropped the Telegram-approval side of that tool (see the `aw-kanban`
> skill: "Here it creates the card and stops; the response says
> `approval_sent: false`"). The only notification this whole audit produces
> is the one Phase 3 sends explicitly. Don't assume a card firing off a
> message — it never does.

### Audit 1 — Silent degradation (run this first)

This workspace's characteristic failure is a component that is *present but
broken*, reporting healthy. `doctor` is the one command built to catch it:

```bash
aw-workspace-cli doctor        # non-zero exit == something is degraded
aw-workspace-cli status
aw-workspace-cli apps --json
```

| Flag | Criterion |
|---|---|
| `degraded:doctor` | `doctor` reports a degraded component |
| `degraded:app-down` | An app is installed and desired but not running |
| `degraded:mcp-tools-missing` | An installed app contributes MCP tools the gateway isn't serving |
| `degraded:cli-broken` | A system CLI is on `PATH` but fails its own verify command |

A `doctor` finding is **P1 by default**. The whole reason it exists is that
these never surface anywhere a human looks.

### Audit 2 — Architecture drift

Compare what is actually installed against what the KB says is installed.

```bash
aw-workspace-cli apps
ls /opt/aw-workspace/repos/
ls /opt/aw-workspace/skills/
```

Then search the KB for each app that looks significant and undocumented.

| Flag | Criterion |
|---|---|
| `arch:undocumented-app` | An installed app with no KB doc |
| `arch:stale-doc` | A KB doc describing an app/service no longer installed |
| `arch:drift` | Documented behaviour contradicted by the code |
| `arch:missing-decision` | A significant design choice with no ADR |
| `arch:orphan-skill` | A skill under `skills/` whose owning app is uninstalled |

`arch:orphan-skill` is worth real attention: a stale skill teaches every
future agent to call tools that no longer exist.

### Audit 3 — Resilience

```bash
grep -rn "except Exception" /opt/aw-workspace/src/ | head -40
grep -rln "httpx\|requests" /opt/aw-workspace/src/ /opt/aw-workspace/repos/aw-app-*/
```

| Flag | Criterion |
|---|---|
| `resilience:silent-failure` | An exception swallowed with no log and no alert |
| `resilience:missing-timeout` | An outbound HTTP call with no timeout |
| `resilience:spof` | A component whose failure removes a user-facing feature, with no fallback |
| `resilience:presence-check` | A health check that only tests for *existence*, not *function* |

`resilience:presence-check` is this codebase's recurring bug — several
"health" checks confirmed a file or binary was present and reported green
while the thing was entirely broken. Look for it specifically.

### Audit 4 — Agent-run failures

The runs going through Agents Platform are the workspace's real workload.

```
list_runs({"limit": 100})
```

Look for: runs with `status: success` but zero tokens (a hard failure
wearing a success label — see the KB), repeated failures for one agent slug,
runs that never reached a terminal state, and non-zero exit codes.

| Flag | Criterion |
|---|---|
| `runs:false-success` | Terminal status `success` with no tokens and no output |
| `runs:agent-broken` | The same agent slug failing 3+ times in 24h |
| `runs:stuck` | A run with no terminal status well past its expected wall time |

Priorities, all audits:

* **P1 / `Alta`** — user-facing breakage, data-loss risk, anything `doctor` flags, or 10+ occurrences in 24h
* **P2 / `Média`** — degraded but working, 3–10 occurrences
* **P3 / `Baixa`** — cosmetic, under 3 occurrences → **report only, no card**

Open cards for P1 and P2 only.

### Opening the card

One call per finding, as soon as you have it. Dedup is handled for you by
`finding_key` — a recurring issue updates its existing card instead of
opening a second one.

```
create_kanban_task({
  "title": "aw-app-tasks: agent_prompt tasks seeded without agent_slug",
  "finding_key": "degraded:tasks-agent-slug-dropped",
  "priority": "Alta",
  "agent_slug": "coder",
  "description": "register_contributed_task drops agent_slug, so an app-contributed agent_prompt task is created unroutable and never dispatches.",
  "input_text": "Forward agent_slug and reuse_session in tasks_app/plugin.py::register_contributed_task.",
  "check_hint": "grep -q 'agent_slug=' /opt/aw-workspace/repos/aw-app-tasks/tasks_app/plugin.py",
  "plan": "## O que foi encontrado\n...\n\n## Como foi detectado\n...\n\n## Impacto\n...\n\n## Plano de execução\n1. ...\n\n## Verificação\n...",
  "source": "system-analyst",
  "tags": ["degraded", "tasks"]
})
```

| Field | Required | Notes |
|---|---|---|
| `title` | ✅ | Short and specific |
| `finding_key` | ✅ | Stable `categoria:assunto-kebab`. This is the dedup identity — same issue must produce the same key on every run |
| `priority` | ✅ | `Alta` / `Média` / `Baixa` |
| `agent_slug` | ✅ | From Step 0's `list_agents`, never invented |
| `description` | ✅ | ≤200 chars. This is what reaches Telegram — make it the sentence that decides whether to act |
| `plan` | — | The card body: what was found (with file:line or command output), how it was detected, impact, numbered fix plan, verification |
| `input_text` | — | What the executor agent is told to do when approved |
| `check_hint` | ✅ | Bash one-liner, exit 0 when fixed. Phase 1 of the *next* run executes this — see the constraints below. **Treat as required**: a card without one can never self-close |
| `source` | — | Always `system-analyst` |
| `tags` | — | Free-form |

**`check_hint` constraints** — it runs inside an *agent* container a day
later, not in your shell now. Every one of these was a real dead hint on this
board, found on 2026-08-19:

* **Available:** `grep`, `test`, `curl`, `python3` (with `cryptography`) and
  `aw-workspace-cli`. **Not available:** `docker ps`, `podman exec`. Write
  hints against files and HTTP endpoints, not the container runtime.
* **Paths.** This workspace is `/opt/aw-workspace`. The retired monolith is a
  *checkout* at `/opt/aw-workspace/repos/agentic-workspace` — bare
  `/opt/agentic-workspace` does not exist and never will. 22 cards pointed
  there and could not validate for months.
* **Reachable hosts.** From an agent container: the workspace API is
  `http://172.18.0.1:9123` and Agents Platform is `http://172.18.0.1:10014`
  (**not** `127.0.0.1`, which is the agent container itself, and not port
  `10005`, which is retired). AP's `/api/*` needs a bearer token — read it
  from `.aw-workspace/app-config/agents-platform-runners.json`.
* **No secrets inline.** Read them from `.aw-workspace/secrets/*.json`
  (Fernet, key at `.aw-workspace/secret.key`) or `app-config/*.json` at run
  time. A hint is stored in Notion in plaintext, forever, in page history.
* **Fail closed.** When the hint cannot tell, it must exit non-zero. A hint
  that greps a whole file for a symbol that exists somewhere else in it, or
  that reports green merely because a path is *absent*, is a false-green —
  this board's single most common defect. Scope the grep to the function or
  line range the finding is actually about.
* **Run it before you save it.** Exit code 2 (bad quoting) and a hint
  truncated by Notion's 2000-char rich_text limit both read as "still broken"
  forever. If it doesn't fit on one line, it's too clever — narrow the check.

Write `description` and `plan` in the language Frederico uses with you —
Portuguese unless he switched.

---

## Phase 3 — Report

> **Corrected 2026-08-16** — this section used to say "a cron-triggered run
> only notifies on failure, so a clean audit is silent by design" and "the
> cards you opened in Phase 2 each fired their own Telegram message". Neither
> was true: `aw-app-tasks` never sets `notified` for an `agent_prompt` task
> (success or failure — verified against every historical run of this task),
> and `create_kanban_task` stopped sending Telegram approvals when aw-kanban
> was decoupled (see the note in Phase 2). The result: a run that found real
> P1s and finished `success` produced **zero** Telegram messages, confirmed
> live via Playwright against Frederico's own Telegram — see Kanban card
> `degraded:system-analyst-no-telegram-delivery` for the full writeup. The
> step below is the actual fix — **do not skip it**, it is currently the
> *only* thing that tells Frederico this audit ran at all.

First, publish the presentation — see the **`aw-presentation`** skill:

```
create_presentation({ "title": "system-analyst — <YYYY-MM-DD>", ... })
```

Structure:

```markdown
## Resumo
- Fase 1: N cards revalidados, X auto-resolvidos, Y não verificáveis
- Fase 2: N findings — X novos, Y recorrentes, Z apenas relatados (P3)

## Degradação silenciosa
## Deriva de arquitetura
## Resiliência
## Falhas de execução de agentes
## P3 — observado, sem card
## Proposto para decisão humana
```

The last two sections carry the value a card can't: what you saw but chose
not to escalate, and what needs a human call (a new agent, a design
decision, something you couldn't verify). Be explicit about what you
**didn't** check and why — a report that hides its own gaps is worse than a
short one.

**Then send the report to Frederico's Telegram — mandatory, every run,**
regardless of whether Phase 2 found anything. Silence here is the bug this
correction exists to fix. Use `Bash` (you always have it) to POST directly
to the Agents Platform's own report endpoint — it needs no auth token, it is
the same call `aw-backend`'s `kanban_manager.send_report()` makes, and it is
reachable from inside your own agent container on the same docker bridge
network aw-app-tasks already documents (`172.18.0.1` = the
`agentic-workspace_default` bridge gateway):

```bash
curl -s -m 15 -X POST http://172.18.0.1:10014/api/telegram/report \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c '
import json, sys
print(json.dumps({"title": sys.argv[1], "text": sys.argv[2]}))
' "system-analyst — $(date +%Y-%m-%d)" "$YOUR_SUMMARY_TEXT")"
```

`$YOUR_SUMMARY_TEXT` is a short version of the Resumo section above (plain
text, a few lines — this is a Telegram message, not the full presentation).
The call returns `{"ok": true, "sent": N}` on success; `N` is how many
sysadmin recipients got it. If the call fails or times out, **say so
explicitly in this run's own final output** (don't retry in a loop — one
attempt, then move on) so it shows up in `get_run_detail` for whoever checks
later. If `172.18.0.1` isn't reachable from your container, note that too —
it's the same address aw-app-tasks's `agents_platform_base` config uses, so
if it changed there it changed here.

Finally: if the audit surfaced a durable lesson about how this workspace
fails, write it to the KB (`update_knowledge_base`). That is what stops the
next run from rediscovering it.

---

## MCP tools

Through `aw-gateway`, tool names are prefixed (`aw__kb__…`,
`aw__aw_kanban__…`). See the **`aw-kanban`** skill for the full Kanban
reference — never hand-roll curl against the gateway.

| Tool | Purpose |
|---|---|
| `create_kanban_task` | Open/update a finding card. **Does not notify** — see Phase 2. |
| `list_kanban_cards`, `move_kanban_task`, `add_kanban_comment` | Phase 1 saneamento |
| `search_knowledge_base`, `update_knowledge_base` | Step 0 grounding, Phase 3 lessons |
| `list_agents`, `list_runs` | Step 0 routing, Audit 4 |
| `create_presentation` | Phase 3 written report |
| `Bash` → `curl .../api/telegram/report` | Phase 3 **actual Telegram notification** — mandatory, see Phase 3 |
