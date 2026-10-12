"""Staging registrar: what a plugin's ``register()`` talks to.

Nothing here touches the real :class:`~tiqora.extensions.Registry`; items are staged and
committed all-or-nothing by :func:`tiqora.plugins.loader.commit_registrar`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import inspect
import re
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Final, Literal

from fastapi import APIRouter
from fastapi.routing import APIRoute
from pydantic import BaseModel

from tiqora.extensions.registry import ExtensionPoint
from tiqora.jobs.registry import JOB_HANDLERS, JobHandler
from tiqora.plugins.context import PluginContext
from tiqora.plugins.df_types import (
    BASE_TYPES,
    DYNAMIC_FIELD_TYPES,
    DfBaseType,
    DfDisplay,
    DfValidate,
    DynamicFieldType,
)
from tiqora.plugins.events import EVENT_SUBSCRIBERS, EventHandler, EventSubscriber
from tiqora.plugins.mail_filters import (
    INBOUND_MAIL_FILTERS,
    OUTBOUND_MAIL_FILTERS,
    MailFilter,
    MailFilterStage,
)
from tiqora.plugins.manifest import PluginManifest
from tiqora.plugins.mcp import CORE_MCP_TOOL_NAMES, MCP_TOOLS, McpTool
from tiqora.plugins.naming import CAMEL_NAME_RE, TAG_NAME_RE, qualified
from tiqora.plugins.number_generators import (
    MAX_PATTERN_LEN,
    TICKET_NUMBER_GENERATORS,
    NumberBuild,
    NumberRequest,
    TicketNumberGenerator,
    anchored_pattern,
    check_number,
)
from tiqora.plugins.placeholders import (
    MAX_TIMEOUT_MS,
    MIN_TIMEOUT_MS,
    PLACEHOLDERS,
    Placeholder,
    PlaceholderResolve,
)
from tiqora.plugins.process_points import (
    MAX_ACTION_TIMEOUT,
    MIN_ACTION_TIMEOUT,
    ConditionRun,
    TransitionActionRun,
    make_action_adapter,
    make_condition_adapter,
    stage_action,
    stage_condition,
)
from tiqora.plugins.routes import PLUGIN_ROUTES, PluginRoute
from tiqora.plugins.sync_hooks import (
    SYNC_HOOKS,
    SYNC_OPERATIONS,
    SyncHandler,
    SyncHook,
    SyncOperation,
)
from tiqora.plugins.tasks import SCHEDULED_TASKS, Schedule, ScheduledTask, TaskRun
from tiqora.process.actions.registry import TRANSITION_ACTIONS, ActionParam
from tiqora.process.condition_modules import CONDITION_MODULES, ZNUNY_VALIDATION_PREFIX
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


def shield_job_run[T](
    run: Callable[[Any, Any], Awaitable[T]],
) -> Callable[[Any, Any], Awaitable[T]]:
    """Wrap plugin job code (``run``, ``on_interrupted``) so ``SystemExit`` & co. become an
    ordinary error (B-I2).

    The job runner maps every ``Exception`` (including ``JobRetry``/``JobFailed``/``JobCancelled``)
    and relies on ``CancelledError`` for its timeout; both pass unchanged. Any other
    ``BaseException`` from plugin code would end the worker's event loop.
    """

    @functools.wraps(run)
    async def shielded(ctx: Any, payload: Any) -> T:
        try:
            return await run(ctx, payload)
        except (Exception, asyncio.CancelledError):
            raise
        except BaseException as exc:  # noqa: BLE001 - sys.exit() in plugin code must not end the worker
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

    return shielded


class RegistrationRejected(ValueError):
    """A registration was refused; the message is German and shown to admins."""


def is_async_callable(fn: object) -> bool:
    """True for ``async def`` functions, partials of them and objects with ``async __call__``."""
    while isinstance(fn, functools.partial):
        fn = fn.func
    if inspect.iscoroutinefunction(fn):
        return True
    return callable(fn) and inspect.iscoroutinefunction(getattr(fn, "__call__", None))  # noqa: B004


def _require_async(fn: object, what: str) -> None:
    if not is_async_callable(fn):
        raise RegistrationRejected(f"{what} muss eine async-Funktion sein (async def)")


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
                return
            # Authentication is attached per APIRoute at mount time; plain Starlette routes,
            # mounts and websockets would bypass it.
            for r in router.routes:
                if not isinstance(r, APIRoute):
                    raise RegistrationRejected(
                        "Nur FastAPI-Routen (@router.get/post …) direkt auf diesem Router "
                        "sind für angemeldete Schnittstellen erlaubt; verschachtelte Router "
                        "(include_router), Starlette-Routen, Mounts und WebSockets "
                        "nur mit Freigabe für öffentliche Schnittstellen"
                    )

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
            _require_async(handler, f"Der Abonnent „{name}“")
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
            _require_async(run, f"Die Aufgabe „{name}“")
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
            _require_async(handler.run, f"Der Job-Handler „{handler.kind}“")
            prefix = f"{self.plugin_id}."
            suffix = handler.kind[len(prefix) :]
            if not handler.kind.startswith(prefix) or not NAME_RE.fullmatch(suffix):
                raise RegistrationRejected(
                    f"Aufgabenart „{handler.kind}“ muss „{prefix}<name>“ lauten "
                    "(<name>: a-z, 0-9 und _)"
                )

        shielded = dataclasses.replace(
            handler,
            run=shield_job_run(handler.run),
            on_interrupted=(
                shield_job_run(handler.on_interrupted)
                if handler.on_interrupted is not None
                else None
            ),
        )
        self._stage(JOB_HANDLERS, shielded, check=check)

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
            from tiqora.plugins.mcp import check_tool_buildable

            try:
                check_tool_buildable(full, fn, description)
            except ValueError as exc:
                raise RegistrationRejected(str(exc)) from exc

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

    def before(
        self,
        operation: SyncOperation,
        name: str,
        handler: SyncHandler,
        *,
        timeout_ms: int = 1000,
        on_error: Literal["allow", "deny"] = "allow",
        order: int = 100,
    ) -> None:
        def check() -> None:
            if operation not in SYNC_OPERATIONS:
                raise RegistrationRejected(
                    f"Unbekannter Vorgang „{operation}“ für eine Prüfung vor dem Speichern; "
                    f"erlaubt: {', '.join(SYNC_OPERATIONS)}"
                )
            self._check_name(name, "die Prüfung vor dem Speichern")
            _require_async(handler, f"Die Prüfung „{name}“")
            if isinstance(timeout_ms, bool) or not 50 <= timeout_ms <= 3000:
                raise RegistrationRejected(
                    "Zeitlimit einer Prüfung muss zwischen 50 und 3000 Millisekunden liegen"
                )
            if on_error not in ("allow", "deny"):
                raise RegistrationRejected(
                    f"on_error „{on_error}“ ist ungültig; erlaubt: allow, deny"
                )
            if isinstance(order, bool) or not 0 <= order <= 1000:
                raise RegistrationRejected(
                    "Reihenfolge einer Prüfung muss zwischen 0 und 1000 liegen"
                )

        item = SyncHook(
            key=f"{self.plugin_id}:{name}",
            plugin_id=self.plugin_id,
            operation=operation,
            handler=handler,
            timeout_ms=timeout_ms,
            on_error=on_error,
            order=order,
            context=self._context,
        )
        self._stage(SYNC_HOOKS, item, check=check)

    def transition_action(
        self,
        name: str,
        run: TransitionActionRun,
        *,
        params: Iterable[ActionParam] = (),
        timeout_seconds: int = 5,
    ) -> str:
        """Stage ``Plugin_<short_id>_<name>`` as a process transition action (D5).

        Returns the qualified name.
        """
        qname = qualified(self._manifest.short_id, name)
        params_t: tuple[Any, ...]
        try:
            params_t = tuple(params)
            params_error = False
        except TypeError:
            params_t, params_error = (), True

        def check() -> None:
            if not isinstance(name, str) or not CAMEL_NAME_RE.fullmatch(name):
                raise RegistrationRejected(
                    f"Ungültiger Name „{name}“ für die Prozessaktion: "
                    "2 bis 41 Zeichen, A-Z, a-z und 0-9, beginnt mit einem Großbuchstaben"
                )
            _require_async(run, f"Die Prozessaktion „{name}“")
            if (
                isinstance(timeout_seconds, bool)
                or not MIN_ACTION_TIMEOUT <= timeout_seconds <= MAX_ACTION_TIMEOUT
            ):
                raise RegistrationRejected(
                    f"Zeitlimit einer Prozessaktion muss zwischen {MIN_ACTION_TIMEOUT} "
                    f"und {MAX_ACTION_TIMEOUT} Sekunden liegen"
                )
            if params_error:
                raise RegistrationRejected(
                    f"Parameter der Prozessaktion „{name}“ müssen eine Liste von ActionParam sein"
                )
            for param in params_t:
                if not isinstance(param, ActionParam):
                    raise RegistrationRejected(
                        f"Parameter der Prozessaktion „{name}“ müssen ActionParam sein"
                    )

        adapter = make_action_adapter(
            plugin_id=self.plugin_id,
            plugin_name=self._manifest.name,
            qualified_name=qname,
            run=run,
            context=self._context,
            timeout_seconds=timeout_seconds,
        )
        self._stage(
            TRANSITION_ACTIONS,
            stage_action(qualified_name=qname, adapter=adapter, params=params_t),
            check=check,
        )
        return qname

    def condition_module(self, name: str, run: ConditionRun) -> str:
        """Stage ``Plugin_<short_id>_<name>`` as a ``Type: Module`` transition condition (D5).

        Returns the full match path. A condition runs synchronously on the event loop while the
        transitions are evaluated: it must be a plain, fast ``def`` (no I/O, no blocking calls).
        """
        match = ZNUNY_VALIDATION_PREFIX + qualified(self._manifest.short_id, name)

        def check() -> None:
            if not isinstance(name, str) or not CAMEL_NAME_RE.fullmatch(name):
                raise RegistrationRejected(
                    f"Ungültiger Name „{name}“ für die Bedingung: "
                    "2 bis 41 Zeichen, A-Z, a-z und 0-9, beginnt mit einem Großbuchstaben"
                )
            if not callable(run) or is_async_callable(run):
                raise RegistrationRejected(
                    f"Die Bedingung „{name}“ muss eine gewöhnliche Funktion sein "
                    "(def, nicht async def): sie läuft synchron und muss schnell sein"
                )

        self._stage(
            CONDITION_MODULES,
            stage_condition(
                match=match,
                adapter=make_condition_adapter(
                    plugin_id=self.plugin_id, qualified_match=match, run=run
                ),
            ),
            check=check,
        )
        return match

    def placeholder(self, name: str, resolve: PlaceholderResolve, *, timeout_ms: int = 500) -> str:
        """Stage ``PLUGIN_<SHORT_ID>_<name>`` as a template tag (D6).

        Returns the tag; templates write ``<TIQORA_PLUGIN_<SHORT_ID>_<name>>`` (or ``<OTRS_…>``).
        """
        short = self._manifest.short_id.upper()
        tag = f"PLUGIN_{short}_{name}"

        def check() -> None:
            from tiqora.channels.email.placeholder import _PASSWORD_RE, _SECRET_RE

            if not isinstance(name, str) or not TAG_NAME_RE.fullmatch(name):
                raise RegistrationRejected(
                    f"Ungültiger Platzhaltername „{name}“: 2 bis 41 Zeichen, "
                    "Großbuchstaben, Ziffern und Unterstrich, beginnt mit einem Großbuchstaben"
                )
            if _SECRET_RE.search(f"PLUGIN_{short}_") or _PASSWORD_RE.search(f"PLUGIN_{short}_"):
                raise RegistrationRejected(
                    f"Der Kurzname „{self._manifest.short_id}“ der Erweiterung enthält ein Wort, "
                    "bei dem Platzhalterwerte als geheim maskiert werden (sie erschienen als „xxx“)"
                )
            if _SECRET_RE.search(tag) or _PASSWORD_RE.search(tag):
                raise RegistrationRejected(
                    f"Platzhaltername „{name}“ würde als geheim maskiert "
                    "(Namen mit config, secret, passw, userpw, auth, token oder pw-Endung)"
                )
            _require_async(resolve, f"Der Platzhalter „{name}“")
            if isinstance(timeout_ms, bool) or not MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS:
                raise RegistrationRejected(
                    f"Zeitlimit eines Platzhalters muss zwischen {MIN_TIMEOUT_MS} "
                    f"und {MAX_TIMEOUT_MS} Millisekunden liegen"
                )

        item = Placeholder(
            tag=tag,
            plugin_id=self.plugin_id,
            resolve=resolve,
            timeout_ms=timeout_ms,
            context=self._context,
        )
        self._stage(PLACEHOLDERS, item, check=check)
        return tag

    def number_generator(
        self,
        name: str,
        build: NumberBuild,
        *,
        label: str,
        pattern: str,
        uses_counter: bool = True,
        date_based: bool = False,
    ) -> str:
        """Stage ``Plugin_<short_id>_<name>`` as a ticket number scheme (D7).

        Returns the qualified name an admin selects. ``build`` is a plain, fast ``def`` (it runs
        in a worker thread with a 1 s limit); ``pattern`` is a regex for the WHOLE number without
        capturing groups, used to recognise the number in a reply's subject.
        """
        qname = qualified(self._manifest.short_id, name)
        item = TicketNumberGenerator(
            key=qname,
            plugin_id=self.plugin_id,
            label=label,
            build=build,
            pattern=pattern,
            uses_counter=uses_counter,
            date_based=date_based,
            context=self._context,
        )

        def check() -> None:
            if not isinstance(name, str) or not CAMEL_NAME_RE.fullmatch(name):
                raise RegistrationRejected(
                    f"Ungültiger Name „{name}“ für das Ticketnummer-Schema: "
                    "2 bis 41 Zeichen, A-Z, a-z und 0-9, beginnt mit einem Großbuchstaben"
                )
            if not isinstance(label, str) or not label.strip() or len(label) > 100:
                raise RegistrationRejected(
                    f"Das Ticketnummer-Schema „{name}“ braucht eine Bezeichnung "
                    "(höchstens 100 Zeichen)"
                )
            if not callable(build) or is_async_callable(build):
                raise RegistrationRejected(
                    f"Das Ticketnummer-Schema „{name}“ muss eine gewöhnliche Funktion sein "
                    "(def, nicht async def): sie läuft in einem Hilfsthread und muss schnell sein"
                )
            if not isinstance(uses_counter, bool) or not isinstance(date_based, bool):
                raise RegistrationRejected(
                    f"uses_counter und date_based des Schemas „{name}“ müssen True oder False sein"
                )
            if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_LEN:
                raise RegistrationRejected(
                    f"Das Muster des Ticketnummer-Schemas „{name}“ muss ein regulärer Ausdruck "
                    f"mit 1 bis {MAX_PATTERN_LEN} Zeichen sein"
                )
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise RegistrationRejected(
                    f"Das Muster des Ticketnummer-Schemas „{name}“ ist kein gültiger "
                    f"regulärer Ausdruck: {exc}"
                ) from exc
            if compiled.groups:
                raise RegistrationRejected(
                    f"Das Muster des Ticketnummer-Schemas „{name}“ darf keine Gruppen "
                    "enthalten (nur (?:…) ist erlaubt)"
                )
            now = datetime.now(UTC)
            try:
                example = build(
                    NumberRequest(
                        counter=1 if uses_counter else None,
                        system_id="10",
                        now_local=now,
                        offset=0,
                    )
                )
            except Exception as exc:  # the plugin's own code
                raise RegistrationRejected(
                    f"Das Ticketnummer-Schema „{name}“ konnte keine Beispielnummer bilden: "
                    f"{type(exc).__name__}"
                ) from exc
            if (reason := check_number(item, example)) is not None:
                raise RegistrationRejected(
                    f"Die Beispielnummer des Ticketnummer-Schemas „{name}“ ist ungültig "
                    f"({_NUMBER_REASONS.get(reason, reason)}): erlaubt sind 1 bis 50 Zeichen aus "
                    "A-Z, a-z, 0-9, Punkt, Unterstrich, Schrägstrich und Bindestrich, "
                    "passend zum Muster"
                )
            # follow-up detection searches the pattern inside a subject, so an anchored one
            # (^…$) would never match there and every reply would open a new ticket
            if not re.search(
                re.escape("X") + anchored_pattern(item.pattern), "X" + str(example), re.IGNORECASE
            ):
                raise RegistrationRejected(
                    f"Das Muster des Ticketnummer-Schemas „{name}“ muss innerhalb eines Betreffs "
                    "passen können (keine Anker wie ^ oder $ verwenden)"
                )

        self._stage(TICKET_NUMBER_GENERATORS, item, check=check)
        return qname

    def dynamic_field_type(
        self,
        name: str,
        *,
        label: str,
        base_type: DfBaseType,
        validate: DfValidate | None = None,
        display: DfDisplay | None = None,
        config_model: type[BaseModel] | None = None,
    ) -> str:
        """Stage ``Plugin_<short_id>_<name>`` as a dynamic field type (D8).

        Returns the qualified name an admin selects. Fields of the type are stored as
        ``base_type``; ``validate`` and ``display`` are plain, fast ``def`` functions (they run
        in a worker thread with a 1 s limit) and ``config_model`` describes the type's settings.
        """
        qname = qualified(self._manifest.short_id, name)
        item = DynamicFieldType(
            key=qname,
            plugin_id=self.plugin_id,
            label=label,
            base_type=base_type,
            validate=validate,
            display=display,
            config_model=config_model,
            context=self._context,
        )

        def check() -> None:
            if not isinstance(name, str) or not CAMEL_NAME_RE.fullmatch(name):
                raise RegistrationRejected(
                    f"Ungültiger Name „{name}“ für den Feldtyp: "
                    "2 bis 41 Zeichen, A-Z, a-z und 0-9, beginnt mit einem Großbuchstaben"
                )
            if not isinstance(label, str) or not label.strip() or len(label) > 100:
                raise RegistrationRejected(
                    f"Der Feldtyp „{name}“ braucht eine Bezeichnung (höchstens 100 Zeichen)"
                )
            if base_type not in BASE_TYPES:
                raise RegistrationRejected(
                    f"Der Basistyp „{base_type}“ des Feldtyps „{name}“ ist nicht erlaubt; "
                    f"erlaubt sind {', '.join(BASE_TYPES)}"
                )
            for what, fn in (("validate", validate), ("display", display)):
                if fn is not None and (not callable(fn) or is_async_callable(fn)):
                    raise RegistrationRejected(
                        f"„{what}“ des Feldtyps „{name}“ muss eine gewöhnliche Funktion sein "
                        "(def, nicht async def): sie läuft in einem Hilfsthread und muss "
                        "schnell sein"
                    )
            if config_model is not None and not (
                isinstance(config_model, type) and issubclass(config_model, BaseModel)
            ):
                raise RegistrationRejected(
                    f"Das config_model des Feldtyps „{name}“ muss eine Pydantic-Modellklasse sein"
                )
            if config_model is not None:
                try:  # the admin listing renders this schema; one broken model must not break it
                    config_model.model_json_schema()
                except Exception as exc:
                    raise RegistrationRejected(
                        f"Das config_model des Feldtyps „{name}“ lässt sich nicht als "
                        f"JSON-Schema beschreiben ({type(exc).__name__})"
                    ) from exc

        self._stage(DYNAMIC_FIELD_TYPES, item, check=check)
        return qname

    def mail_filter(
        self,
        name: str,
        stage: MailFilterStage,
        run: Callable[..., Awaitable[None]],
        *,
        order: int = 100,
        timeout_seconds: int = 5,
    ) -> None:
        """Stage a mail filter (D9): ``pre``/``post`` for incoming, ``outbound`` for outgoing mail.

        ``run`` is ``async def (ctx: MailFilterContext, mail)`` where ``mail`` is an
        ``InboundMail`` (pre), ``InboundMailResult`` (post) or ``OutboundMail`` (outbound).
        Inbound filters are fail-open; an outbound filter may only add ``X-`` headers or veto.
        """
        point = OUTBOUND_MAIL_FILTERS if stage == "outbound" else INBOUND_MAIL_FILTERS

        def check() -> None:
            if stage not in ("pre", "post", "outbound"):
                raise RegistrationRejected(
                    f"Unbekannte Stufe „{stage}“ für einen Mailfilter; erlaubt: pre, post, outbound"
                )
            self._check_name(name, "den Mailfilter")
            _require_async(run, f"Der Mailfilter „{name}“")
            if isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 30:
                raise RegistrationRejected(
                    "Zeitlimit eines Mailfilters muss zwischen 1 und 30 Sekunden liegen"
                )
            if isinstance(order, bool) or not 0 <= order <= 1000:
                raise RegistrationRejected(
                    "Reihenfolge eines Mailfilters muss zwischen 0 und 1000 liegen"
                )

        if stage not in ("pre", "post", "outbound"):
            # Reject before choosing a point: the capability to check depends on the stage.
            check()
        item = MailFilter(
            key=f"{self.plugin_id}:{name}",
            plugin_id=self.plugin_id,
            stage=stage,
            run=run,
            order=order,
            timeout_seconds=timeout_seconds,
            context=self._context,
        )
        self._stage(point, item, check=check)


_NUMBER_REASONS: Final = {
    "not_a_string": "keine Zeichenkette",
    "empty": "leer",
    "too_long": "länger als 50 Zeichen",
    "bad_characters": "unzulässige Zeichen",
    "pattern_mismatch": "passt nicht zum Muster",
}
