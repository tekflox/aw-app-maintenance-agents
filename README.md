# aw-app-maintenance-agents

The workspace's own maintenance crew, packaged as one installable unit.

Installing this app gives you:

| What | Where it comes from |
|---|---|
| **System Analyst** agent | `contributes.agents` → Agents Platform |
| **`aw-system-analyst`** skill | `contributes.skills` → `skills/` |
| **System Analyst — daily audit** (06:00, `agent_prompt`) | `contributes.tasks` → aw-app-tasks |
| **Workspace doctor — degradation watch** (every 4h, `agentic_output`) | `contributes.tasks` |

Both schedules install **disabled**. Enable them in Tasks once you've run the
audit by hand at least once.

## Why an app

Before the contribution surfaces, "an agent" here was three unrelated objects
someone created by hand, in order, across two UIs: an Agents Platform agent
row, a skill file under `skills/`, and a scheduled task naming the agent's
slug. Nothing linked them and nothing checked them. When the System Analyst
was ported out of the monolith, the schedule came across and the agent didn't
— so the task fired into a slug that did not exist, and said nothing.

This app is the fix for that class of problem: the three are declared
together, validated together at install time, and arrive together.

## What the analyst does

Read `skills/aw-system-analyst/SKILL.md` for the contract. In short — three
phases:

1. **Saneamento** — re-run the `check_hint` on every card it opened before,
   close what fixed itself.
2. **Scan** — four audits (silent degradation via `aw-workspace-cli doctor`,
   architecture drift vs the KB, resilience gaps, Agents Platform run
   failures), opening one Kanban card per P1/P2 finding as it goes.
3. **Report** — a presentation summarising the run, including what it
   *didn't* check.

It opens cards. It does not fix anything — every finding is routed to an
executor agent (`coder`, `doc-writer`, `debugger`, `planner`) and waits for a
human to approve.

## Ported from the monolith, not copied

The original (`repos/agentic-workspace/skills/aw-system-analyst/SKILL.md`)
was written against infrastructure this workspace doesn't have:

| Monolith | Here |
|---|---|
| SigNoz MCP — OTel logs, traces, alert history (Audits 3, 4, 4b, 5) | **Not ported.** No SigNoz in this workspace. Replaced by `doctor` + Agents Platform run history |
| Notion REST by `curl`, token hardcoded in the skill | `aw-kanban` MCP tools |
| `awserv` at `127.0.0.1:9123` (`create-task`, `send-report`) | `create_kanban_task`; report via `create_presentation` |
| `agents-platform` at `127.0.0.1:10005` | `list_agents` / `list_runs` MCP tools |
| `/opt/agentic-workspace` | `/opt/aw-workspace` |
| Created new executor agents on the fly | Doesn't. Proposes them in the report instead |

If a SigNoz app ever lands here, the old queries are worth reviving from that
file — they're good, they just have nothing to talk to today.

## Known gaps

* **A successful cron run is silent.** `aw-app-tasks` only notifies when a
  scheduled run *fails* (`manager.py`, the `trigger != "cron"` guard), so the
  audit's own delivery is the cards it opens plus its presentation — not the
  task notification.
* **`aw-autoskill` is not ported yet.** It's the other monolith maintenance
  agent that belongs in this app; it depends on monolith session paths and
  the `telegram_messages` table, so it needs real work rather than a
  retarget.

## How the agent gets its tools

The analyst is useless without the MCP gateway — no gateway, no Kanban to
file findings into and no knowledge base to check them against. But the
gateway entry is `{url, headers: {Authorization: Bearer <token>}}`, and this
manifest ships to a marketplace, so it cannot carry that.

So the manifest declares the server **by name**:

```json
"agent_configs": [
  { "slug": "maintenance-agents-config", "mcp_servers": ["aw-gateway"] }
]
```

and `aw-app-agents-platform-runners` — which is already the provider for
`contributes.agents`, and already lives inside the workspace that owns the
secret — resolves that name against the workspace's own `.mcp.json` at seed
time. The intention travels in the manifest; the credential never leaves the
machine.

One wrinkle worth knowing: the URL in `.mcp.json`
(`http://aw-app-mcp-gateway:9200/mcp`) is the *workspace container's* view,
where the gateway is a compose peer. A spawned agent container is a sibling
in the nested podman namespace and can't resolve that name — it needs the
bridge gateway IP. Same gateway, same token, different address, so the
address is the one configurable part: `gateway_mcp_url` on the runners app,
defaulting to `http://172.18.0.1:9200/mcp` (the value every hand-configured
agent on this tenant already carries).

Nothing to paste by hand, including on a workspace created from scratch.

## Requires

`contributes.tasks` with `type: "agent_prompt"` needs:

* **aw-workspace** with `agent_prompt` in `CONTRIBUTED_TASK_TYPES`
  (`src/apps/manifest.py`, 2026-08-13) — older cores reject this manifest at
  install with *"type must be 'terminal' or 'agentic_output'"*.
* **aw-app-tasks ≥ the same date**, whose `register_contributed_task`
  forwards `agent_slug`. An older one seeds the row with a NULL slug and it
  never dispatches — the exact silent failure this app exists to prevent.

## Tests

```bash
python3 -m pytest -c /dev/null tests/ -q
```
