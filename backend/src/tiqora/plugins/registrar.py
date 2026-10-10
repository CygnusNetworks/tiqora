"""Staging registrar: what a plugin's ``register()`` talks to.

Nothing here touches the real :class:`~tiqora.extensions.Registry`; items are staged and
committed all-or-nothing by :func:`tiqora.plugins.loader.commit_registrar`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Final

from fastapi import APIRouter
from pydantic import BaseModel

from tiqora.extensions.registry import ExtensionPoint
from tiqora.jobs.registry import JOB_HANDLERS, JobHandler
from tiqora.plugins.context import PluginContext
from tiqora.plugins.events import EVENT_SUBSCRIBERS, EventHandler, EventSubscriber
from tiqora.plugins.manifest import PluginManifest
from tiqora.plugins.mcp import CORE_MCP_TOOL_NAMES, MCP_TOOLS, McpTool
from tiqora.plugins.routes import PLUGIN_ROUTES, PluginRoute
from tiqora.plugins.tasks import SCHEDULED_TASKS, Schedule, ScheduledTask, TaskRun
from tiqora.settings_sections.section import (
    SETTINGS_SECTIONS,
    SettingsSection,
    UnsupportedSettingsSchema,
    validate_section_schema,
)

NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_EVENT_RE: Final = re.compile(r"^(\*|[A-Za-z][A-Za-z0-9_]*\*?)$")
PUBLIC_ROUTES_CAPABILITY: Final = "core.http.public_routes"
MAX_SETTINGS_KEY_LEN: Final = 200
MAX_TIMEOUT_SECONDS: Final = 300


class RegistrationRejected(ValueError):
    """A registration was refused; the message is German and shown to admins."""


class PluginRegistrar:
    """Implements :class:`tiqora_sdk.PluginRegistry`."""

    def __init__(self, manifest: PluginManifest, context: PluginContext) -> None:
        self._manifest = manifest
        self._context = context
        self._staged: list[tuple[ExtensionPoint[Any], Any]] = []
        self._route_count = 0

    @property
    def plugin_id(self) -> str:
        return self._manifest.id

    @property
    def context(self) -> PluginContext:
        return self._context

    @property
    def staged(self) -> tuple[tuple[ExtensionPoint[Any], Any], ...]:
        return tuple(self._staged)

    # -- internal staging path ------------------------------------------------------------

    def _stage(
        self,
        point: ExtensionPoint[Any],
        item: Any,
        *,
        check: Callable[[], None] | None = None,
    ) -> None:
        """Check order: (1) sdk_stable, (2) declared, (3)/(4) ``check``, (5) staged duplicate."""
        if not point.sdk_stable:
            raise RegistrationRejected(
                f"Erweiterungspunkt {point.key} ist für Erweiterungen nicht freigegeben"
            )
        self._require_declared(point.key)
        if check is not None:
            check()
        key = point.item_key(item)
        if any(p.key == point.key and p.item_key(i) == key for p, i in self._staged):
            raise RegistrationRejected(f"{point.key}: „{key}“ wurde bereits registriert")
        self._staged.append((point, item))

    def _require_declared(self, capability: str) -> None:
        if capability not in self._manifest.capabilities.extension_points:
            raise RegistrationRejected(
                f"Erweiterungspunkt {capability} ist im Manifest nicht angegeben"
            )

    def _check_name(self, name: str, what: str) -> None:
        if not NAME_RE.fullmatch(name):
            raise RegistrationRejected(
                f"Ungültiger Name „{name}“ für {what}: "
                "2 bis 41 Zeichen, a-z, 0-9 und _, beginnt mit einem Buchstaben"
            )

    @staticmethod
    def _check_timeout(timeout_seconds: int) -> None:
        if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise RegistrationRejected(
                f"Zeitlimit muss zwischen 1 und {MAX_TIMEOUT_SECONDS} Sekunden liegen"
            )

    # -- public registration API ----------------------------------------------------------

    def settings(self, model: type[BaseModel]) -> SettingsSection:
        pid = self.plugin_id
        section = SettingsSection(
            key=f"plugins.{pid}",
            model=model,
            title_key=f"plugin.{pid}:settings.title",
            i18n_prefix=f"plugin.{pid}:settings",
        )

        def check() -> None:
            if any(p is SETTINGS_SECTIONS for p, _ in self._staged):
                raise RegistrationRejected(
                    "Die Erweiterung darf nur einen Einstellungsbereich anlegen: "
                    "Einstellungen wurden bereits registriert"
                )
            for field_name in model.model_fields:
                if len(f"{section.key}.{field_name}") > MAX_SETTINGS_KEY_LEN:
                    raise RegistrationRejected(f"Einstellungsname zu lang: {field_name}")
            try:
                validate_section_schema(section)
            except UnsupportedSettingsSchema as exc:
                raise RegistrationRejected(f"Einstellungen nicht darstellbar: {exc}") from exc

        self._stage(SETTINGS_SECTIONS, section, check=check)
        return section

    def route(self, router: APIRouter, *, public: bool = False) -> None:
        def check() -> None:
            if public:
                self._require_declared(PUBLIC_ROUTES_CAPABILITY)

        item = PluginRoute(
            key=f"{self.plugin_id}:{self._route_count}",
            plugin_id=self.plugin_id,
            router=router,
            public=public,
            context=self._context,
        )
        self._stage(PLUGIN_ROUTES, item, check=check)
        self._route_count += 1

    def on_event(
        self,
        name: str,
        events: Iterable[str],
        handler: EventHandler,
        *,
        timeout_seconds: int = 30,
    ) -> None:
        if isinstance(events, str):
            raise RegistrationRejected(
                f"Ereignisse für „{name}“ müssen als Liste angegeben werden, nicht als Text"
            )
        names = frozenset(events)

        def check() -> None:
            self._check_name(name, "den Abonnenten")
            self._check_timeout(timeout_seconds)
            if not names:
                raise RegistrationRejected("Mindestens ein Ereignis ist nötig")
            for event in names:
                if not _EVENT_RE.fullmatch(event):
                    raise RegistrationRejected(f"Ungültiger Ereignisname „{event}“")

        item = EventSubscriber(
            key=f"{self.plugin_id}:{name}",
            plugin_id=self.plugin_id,
            events=names,
            handler=handler,
            timeout_seconds=timeout_seconds,
            context=self._context,
        )
        self._stage(EVENT_SUBSCRIBERS, item, check=check)

    def scheduled_task(
        self,
        name: str,
        run: TaskRun,
        *,
        every_seconds: int | None = None,
        daily_at: str | None = None,
        cron: str | None = None,
        timeout_seconds: int = 300,
    ) -> None:
        schedule = Schedule(every_seconds=every_seconds, daily_at=daily_at, cron=cron)

        def check() -> None:
            self._check_name(name, "die Aufgabe")
            try:
                schedule.validate()
            except ValueError as exc:
                raise RegistrationRejected(str(exc)) from exc
            self._check_timeout(timeout_seconds)

        item = ScheduledTask(
            key=f"{self.plugin_id}:{name}",
            plugin_id=self.plugin_id,
            schedule=schedule,
            run=run,
            timeout_seconds=timeout_seconds,
            context=self._context,
        )
        self._stage(SCHEDULED_TASKS, item, check=check)

    def job_handler(self, handler: JobHandler) -> None:
        def check() -> None:
            prefix = f"{self.plugin_id}."
            suffix = handler.kind[len(prefix) :]
            if not handler.kind.startswith(prefix) or not NAME_RE.fullmatch(suffix):
                raise RegistrationRejected(
                    f"Aufgabenart „{handler.kind}“ muss „{prefix}<name>“ lauten "
                    "(<name>: a-z, 0-9 und _)"
                )

        self._stage(JOB_HANDLERS, handler, check=check)

    def mcp_tool(
        self,
        name: str,
        fn: Callable[..., Awaitable[Any]],
        *,
        description: str,
        write: bool = False,
        timeout_seconds: int = 60,
    ) -> None:
        full = f"{self._manifest.short_id}_{name}"

        def check() -> None:
            self._check_name(name, "das MCP-Werkzeug")
            if full in CORE_MCP_TOOL_NAMES:
                raise RegistrationRejected(f"Werkzeugname „{full}“ ist bereits von Tiqora belegt")
            self._check_timeout(timeout_seconds)

        item = McpTool(
            name=full,
            plugin_id=self.plugin_id,
            fn=fn,
            description=description,
            write=write,
            timeout_seconds=timeout_seconds,
            context=self._context,
        )
        self._stage(MCP_TOOLS, item, check=check)
