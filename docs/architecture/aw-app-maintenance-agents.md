---
repo: architecture
path: docs/architecture/aw-app-maintenance-agents.md
source: generated
edited: false
checksum: sha256:a5d63bd88c9c1e3bc5ea59615c39b1348d783408b4044d84134f5cb100cea71c
---
# Maintenance Agents

- **repo**: aw-app-maintenance-agents
- **layer**: app
- **technologies**: python
- **health** (derived): planned

The workspace's own maintenance crew, shipped as one installable unit: the System Analyst agent (daily architecture / silent-degradation / resilience / agent-run audit that opens a Kanban card per finding), the skill defining its contract, and the schedules that drive it — plus a cheap `aw-workspace-cli doctor` watch that only speaks up when something is actually degraded. Ported from the agentic-workspace monolith's aw-system-analyst skill, retargeted from SigNoz/awserv/Notion-by-curl onto the surfaces this workspace actually has.

## Connections
- `other` → **aw-app-agents-platform-runners** — Provides the contributes
- `other` → **aw-app-kb** — Supplies search_knowledge_base/load_skill
- `other` → **aw-app-notion** — Supplies the aw-kanban MCP server the analyst opens finding cards through
- `other` → **aw-app-tasks** — Provides the contributes

## MCP tools
_none exposed_

## Requirements
_none documented_
