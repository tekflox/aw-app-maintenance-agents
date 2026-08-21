"""The manifest IS this app.

There is no route, no window and no CLI here — everything the app delivers
is a declaration in ``aw-app.json`` that some other component seeds. So the
only thing worth testing is that those declarations are well-formed and
internally consistent, which is exactly what breaks silently otherwise: a
task naming an agent that isn't declared seeds fine and then dispatches to
nobody, forever, at 06:00.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
MANIFEST = APP_DIR / "aw-app.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def test_manifest_is_valid_json_with_the_expected_identity(manifest):
    assert manifest["id"] == "maintenance-agents"
    assert manifest["manifest_version"] == 1


def test_declares_the_capabilities_its_contributions_need(manifest):
    # Core rejects contributes.agents/tasks without these — a missing one
    # fails the install rather than silently dropping the contribution.
    perms = set(manifest["permissions"])
    assert {"agents:contribute", "tasks:contribute"} <= perms


def test_every_agent_prompt_task_names_a_declared_agent(manifest):
    """The join this app exists to guarantee.

    Splitting the agent and its schedule across two systems is what made the
    monolith's version of this fail: the task existed, the agent didn't, and
    nothing anywhere connected the two.
    """
    declared = {a["slug"] for a in manifest["contributes"]["agents"]["agents"]}
    for task in manifest["contributes"]["tasks"]:
        if task.get("type") == "agent_prompt":
            assert task["agent_slug"] in declared, (
                f"task {task['name']!r} dispatches to {task['agent_slug']!r}, "
                f"which this app does not declare"
            )


def test_every_agent_skill_slug_is_shipped_here_or_by_a_declared_dependency(manifest):
    """A skill_slug that resolves to nothing is the quiet failure: the agent
    still runs, just with no contract — which reads as a bad model, not a
    missing file.

    `aw-kb-curator` is the one exception, and it is a deliberate one: the
    `kb` app owns that contract because it owns the knowledge base the
    curator audits. Duplicating it here would give two apps a copy that
    drifts. The agent lives here instead because it runs on this app's
    config and its own scheduled task, and `kb` is already a declared
    dependency — non-required, so without it the agent seeds and simply has
    no contract to load, same degraded-not-broken shape as the analyst
    without Notion.
    """
    shipped = {s["id"] for s in manifest["contributes"]["skills"]}
    from_dependency = {"aw-kb-curator"}
    depends_on = {d["id"] for d in manifest["dependencies"]["apps"]}
    assert "kb" in depends_on
    for agent in manifest["contributes"]["agents"]["agents"]:
        for slug in agent.get("skill_slugs", []):
            assert slug in shipped or slug in from_dependency, (
                f"{agent['slug']} references skill {slug!r}, which nothing ships"
            )


def test_the_autoskill_skill_ships_the_script_it_cannot_run_without(manifest):
    """aw-autoskill's SKILL.md invokes compile_sessions.py by path, and the
    skill materialiser copytree's the whole directory — so the script rides
    along only because it sits next to SKILL.md. Ship one without the other
    and the agent reads a contract whose first instruction is a file that
    isn't there.
    """
    entry = next(s for s in manifest["contributes"]["skills"] if s["id"] == "aw-autoskill")
    skill_dir = (APP_DIR / entry["path"]).parent
    assert (skill_dir / "compile_sessions.py").is_file()


def test_referenced_files_exist(manifest):
    for skill in manifest["contributes"]["skills"]:
        assert (APP_DIR / skill["path"]).is_file(), skill["path"]
    for agent in manifest["contributes"]["agents"]["agents"]:
        ref = agent.get("system_prompt_file")
        if ref:
            assert (APP_DIR / ref).is_file(), ref


def test_agents_reference_a_declared_or_preexisting_config(manifest):
    spec = manifest["contributes"]["agents"]
    configs = {c["slug"] for c in spec.get("agent_configs", [])}
    for agent in spec["agents"]:
        assert agent["agent_config_slug"] in configs


def test_no_agent_config_ships_an_mcp_bearer_token(manifest):
    """A manifest goes to a marketplace; a gateway token must not ride along.

    The aw-gateway entry on the live agent-config carries a bearer token, so
    this app declares the config WITHOUT mcp_config and relies on
    create-if-absent leaving the real one alone.
    """
    spec = manifest["contributes"]["agents"]
    for cfg in spec.get("agent_configs", []):
        assert "mcp_config" not in cfg
    # Scan the values that actually get POSTed to Agents Platform, not the
    # manifest text — the descriptions here legitimately *discuss* tokens.
    for kind in ("models", "agent_configs", "groups", "agents"):
        for entry in spec.get(kind, []):
            payload = {k: v for k, v in entry.items() if k != "description"}
            blob = json.dumps(payload).lower()
            for marker in ("bearer ", "authorization", "ntn_", "sk-", "token"):
                assert marker not in blob, f"{kind} {entry.get('slug')!r}: {marker}"


def test_seeded_schedules_are_disabled_by_default(manifest):
    # A task that starts firing the moment the app installs is a surprise;
    # the schedule is a suggestion the user opts into.
    for task in manifest["contributes"]["tasks"]:
        assert task.get("enabled", False) is False, task["name"]


def test_the_skill_does_not_carry_monolith_paths_or_secrets():
    """The port's whole point.

    The monolith skill hardcoded a Notion token and pointed at
    /opt/agentic-workspace, awserv on :9123 and agents-platform on :10005 —
    none of which exist here. One reference survives on purpose (a pointer
    to the original file), so assert on the live call shapes, not the word.
    """
    text = (APP_DIR / "skills/aw-system-analyst/SKILL.md").read_text()
    assert "ntn_" not in text, "Notion token leaked into the skill"
    assert "127.0.0.1:9123" not in text
    assert "127.0.0.1:10005" not in text
    assert "/opt/aw-workspace" in text
