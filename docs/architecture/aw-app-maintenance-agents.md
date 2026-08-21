---
repo: architecture
path: docs/architecture/aw-app-maintenance-agents.md
source: generated
edited: false
checksum: sha256:8aa3865f57f192198aad86f07018c7f1231faf15aad821da88a9ee061f58cf62
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
### Toda task agent_prompt semeada nomeia um agente que o app declara
- Given o app entrega o agente system-analyst e a schedule que o dispara como duas declarações separadas do mesmo manifesto
- When o join entre contributes.tasks e contributes.agents.agents é verificado (repos/aw-app-maintenance-agents/tests/test_manifest.py::test_every_agent_prompt_task_names_a_declared_agent:39)
- Then todo task.agent_slug de tipo agent_prompt está entre os agentes declarados — é a junção que justifica o app existir: no monolito agente e schedule moravam em sistemas diferentes, a task existia, o agente não, e nada em lugar nenhum ligava os dois. O sintoma era uma schedule que disparava no vazio às 06:00, todo dia, sem erro nenhum
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-maintenance-agents/tests/test_manifest.py` (passing)

### Schedule semeada por install nasce desligada
- Given o app declara tasks agendadas em contributes.tasks e é instalado sem ninguém escolher horário
- When o default de cada declaração é conferido (repos/aw-app-maintenance-agents/tests/test_manifest.py::test_seeded_schedules_are_disabled_by_default:98)
- Then toda task tem enabled=False — o agendamento embarcado é sugestão, não decisão. Uma task que começa a rodar no instante do install executa trabalho que ninguém pediu num horário que ninguém escolheu, e como ela roda em background isso só é percebido pelo efeito colateral
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-maintenance-agents/tests/test_manifest.py` (passing)

### Nenhum agent_config declarado leva bearer token do gateway
- Given o agent-config vivo carrega um bearer token na entrada do aw-gateway, e este manifesto é publicado num marketplace
- When os valores POSTados para a Agents Platform são varridos, ignorando o campo description (repos/aw-app-maintenance-agents/tests/test_manifest.py::test_no_agent_config_ships_an_mcp_bearer_token:78)
- Then nenhum agent_config traz a chave mcp_config e nenhum valor contém "bearer ", "authorization", "ntn_", "sk-" ou "token" — o app declara o config sem credencial e conta com o create-if-absent deixar o config real intacto. Note que a régua aqui é mais estrita que a do aw-app-devteam, que permite a palavra "token" em valores: os dois testes divergem de propósito porque este app não declara nada onde ela caiba
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-maintenance-agents/tests/test_manifest.py` (passing)

### O skill portado não aponta para endereços do monolito nem carrega segredo
- Given o skill aw-system-analyst veio do monolito, onde tinha um token do Notion embutido e apontava para /opt/agentic-workspace, awserv em :9123 e a agents-platform em :10005
- When o texto entregue é conferido (repos/aw-app-maintenance-agents/tests/test_manifest.py::test_the_skill_does_not_carry_monolith_paths_or_secrets:105, sobre skills/aw-system-analyst/SKILL.md)
- Then não há "ntn_", nem 127.0.0.1:9123, nem 127.0.0.1:10005, e /opt/aw-workspace aparece — a asserção é sobre as formas de chamada vivas, não sobre a palavra, porque uma referência ao arquivo original sobrevive de propósito no texto. É a diferença entre um skill portado e um skill copiado: o copiado ensina o agente a chamar o que não existe mais aqui, e o agente relata sucesso mesmo assim
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-maintenance-agents/tests/test_manifest.py` (passing)
