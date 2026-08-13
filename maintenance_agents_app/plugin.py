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

log = logging.getLogger("aw_apps.maintenance_agents")


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

    async def deactivate(self) -> None:
        log.info("aw-app-maintenance-agents deactivated")
