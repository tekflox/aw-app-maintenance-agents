"""
Entrypoint referenced by aw-app.json's runtime.entrypoint
("maintenance_agents_app.plugin:MaintenanceAgentsAppPlugin").

This app is deliberately almost empty. Everything it delivers — the
``system-analyst`` agent, the config bundle it runs under, the skill that
tells it what to do, and the schedule that fires it — is *declared* in
``aw-app.json`` and seeded by the workspace's own contribution surfaces
(``contributes.agents``, ``contributes.skills``, ``contributes.tasks``).
There is no HTTP route, no window and no CLI, so there is nothing for
``activate`` to register.

Why the app exists at all, then: before the contribution surfaces, an
"agent" in this workspace was three unrelated things a person had to create
by hand, in order, in two different UIs — an Agents Platform agent row, a
skill file under ``skills/``, and a scheduled task pointing at the agent's
slug. Nothing linked them, nothing checked them, and the failure mode when
one was missing was a schedule that fired into nothing. Packaging the three
as one installable unit is the point; the Python here is just the hook the
framework needs to hang that manifest on.

Seed-once, never updated (see aw-workspace's ``src/apps/agents.py`` and
``src/apps/tasks.py``): re-installing this app will not overwrite a system
prompt or a schedule the user has since tuned. Shipping a corrected prompt
means a new slug, or an edit in the UI.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("aw_apps.maintenance_agents")

#: Where ``aw-autoskill`` writes the skills it generates. Under the app's own
#: durable data dir, which is per-workspace — and a workspace is one tenant, so
#: one tenant's generated skills can never reach another's. They used to land in
#: ``native-skills/``, which is this repo's *product* tree: correct-looking,
#: publicly committed, and shared by every deployment.
AUTOSKILL_SKILLS_SUBDIR = os.path.join("aw-autoskill", "skills")


def _autoskill_store() -> str:
    """Resolve the generated-skills dir the same way core resolves app data.

    Mirrors ``src/apps/paths.workspace_home_path()`` off the environment
    rather than importing it: this app has no runtime surface and no reason to
    take a hard dependency on a core module path. The fallback matters — an
    agent-runner container shares the workspace mount but not the server's
    env, so ``AW_WORKSPACE_HOME`` can legitimately be unset.
    """
    home = os.environ.get("AW_WORKSPACE_HOME")
    if not home:
        root = os.path.realpath(
            os.environ.get("AW_WORKSPACE_CONTAINER_DIR", "/opt/aw-workspace")
        )
        home = os.path.join(root, ".aw-workspace")
    return os.path.join(home, "data", AUTOSKILL_SKILLS_SUBDIR)


class MaintenanceAgentsAppPlugin:
    """Tier-1 in-process plugin with no runtime surface of its own."""

    def __init__(self, ctx=None):
        self.ctx = ctx

    async def activate(self, ctx=None) -> None:
        if ctx is not None:
            self.ctx = ctx
        # The contribution registries run from the framework's own activation
        # path, not from here — an app declares, the runtime dispatches. All
        # this needs to do is come up cleanly so that dispatch happens.
        log.info(
            "aw-app-maintenance-agents active — agents/skills/tasks are seeded "
            "by the workspace from aw-app.json's contributes block"
        )

    async def list_skill_sources(self, ctx=None) -> dict | None:
        """Point ``materialize()`` at the skills ``aw-autoskill`` generates.

        These cannot go in ``contributes.skills``: that surface is copied in
        once, at activate, from files shipped in this package — and these are
        written every night, after install, by the agent this app ships.

        Returning ``ok: False`` when the directory is missing would be wrong:
        "not created yet" is a genuine empty, not a failure, and reporting a
        failure would freeze the delete pass and strand skills the user has
        since removed. A real failure here is *not being able to tell* — an
        unreadable data dir — which is what the except branch reports.
        """
        try:
            root = _autoskill_store()
            os.makedirs(root, exist_ok=True)
        except Exception as exc:  # noqa: BLE001 - unreadable dir => "I can't answer"
            log.warning("maintenance-agents: cannot reach the autoskill store: %s", exc)
            return {"ok": False}
        return {"ok": True, "dirs": [root]}

    async def deactivate(self) -> None:
        log.info("aw-app-maintenance-agents deactivated")
