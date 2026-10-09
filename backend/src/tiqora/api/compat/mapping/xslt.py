"""Port of ``Kernel/GenericInterface/Mapping/XSLT.pm`` on lxml (libxslt), sandboxed.

Sandbox: the stylesheet runs with ``XSLTAccessControl.DENY_ALL`` (no file/network read or write,
no ``document()``, no ``exsl:document``); the only external resource a template may load is the
bundled ``OTRSFunctions.xsl``, served by :class:`_OtrsFunctionsResolver` for any href ending in
that file name. Every other import/include fails. XML parsing never resolves entities or DTDs.

Resource bounds: libxslt stops runaway template recursion itself (depth limit, reported as
"Could not map data"); the produced document is capped at ``MAX_OUTPUT_BYTES``. A wall-clock
limit is not possible in-process (libxslt cannot be interrupted); callers run ``map`` in a worker
thread (``asyncio.to_thread``) so a slow template never blocks the event loop.
"""

from __future__ import annotations

import copy
import threading
from typing import Any
from urllib.parse import urlparse

import regex
from lxml import etree

from tiqora.api.compat.mapping import DataInclude, MappingConfigError, MappingError
from tiqora.api.compat.mapping.otrs_functions import OTRS_FUNCTIONS_XSL
from tiqora.api.compat.mapping.perl import (
    REGEX_TIMEOUT_SECONDS,
    compile_perl_regex,
    perl_replacement,
    perl_str,
    perl_truthy,
)
from tiqora.api.compat.mapping.xml_simple import xml_in, xml_out

MAX_OUTPUT_BYTES = 256 * 1024 * 1024
_OTRS_FUNCTIONS_FILE = "OTRSFunctions.xsl"
_NOT_HASH_OR_ARRAY = "Got Data but it is not a hash or array ref in Mapping XSLT backend!"
_INCLUDE_NOT_HASH = "Got DataInclude but it is not a hash ref in Mapping XSLT backend!"


# --------------------------------------------------------------------- template

_AFTER_TAG = regex.compile(r">[ \t\n]+(?=[^< \t\n])")
_BEFORE_TAG = regex.compile(r"(?<![> \t\n])[ \t\n]+<")


def prepare_template(template: str) -> str:
    """Strip template line breaks/indentation between tags (Znuny bug#14106)."""
    template = _AFTER_TAG.sub(">", template)
    return str(_BEFORE_TAG.sub("<", template))


class _OtrsFunctionsResolver(etree.Resolver):  # type: ignore[misc]
    """Serves the bundled OTRSFunctions.xsl for any install path; refuses everything else."""

    def resolve(self, url: str, pubid: str, context: Any) -> Any:
        if urlparse(url).path.endswith(_OTRS_FUNCTIONS_FILE):
            return self.resolve_string(OTRS_FUNCTIONS_XSL, context)
        raise MappingError(f"External file {url!r} is not allowed in Tiqora")


def _parser(huge_xml: bool, *, with_resolver: bool = False) -> Any:
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=huge_xml,
        encoding="utf-8",  # we always hand over utf-8 bytes; ignore any declared encoding
    )
    if with_resolver:
        parser.resolvers.add(_OtrsFunctionsResolver())
    return parser


# ------------------------------------------------------------------ regex filter


def _filters_check(name: str, filters: Any) -> list[dict[str, str]]:
    if not filters:
        return []
    if not isinstance(filters, list):
        raise MappingConfigError(f"{name} is no array ref")
    checked: list[dict[str, str]] = []
    for entry in filters:
        if not isinstance(entry, dict) or not perl_truthy(entry.get("Search")):
            raise MappingConfigError(f"{name} entries need a Search pattern")
        search = perl_str(entry["Search"])
        compile_perl_regex(search, verbose=False)  # eager: MappingConfigError when broken
        checked.append({"Search": search, "Replace": perl_str(entry.get("Replace"))})
    return checked


def regex_recursion(data: Any, filters: list[dict[str, str]]) -> None:
    """Port of ``_RegExRecursion`` (in place; only dicts are processed, keys only)."""
    if not isinstance(data, dict) or not data:
        return
    for key in sorted(data, key=str):
        value = data[key]
        if isinstance(value, list) and value:
            for entry in value:
                regex_recursion(entry, filters)
        else:
            regex_recursion(value, filters)

        for flt in filters:
            pattern = compile_perl_regex(flt["Search"], verbose=False)
            try:
                if pattern.search(key, timeout=REGEX_TIMEOUT_SECONDS) is None:
                    continue
                new_key = pattern.sub(
                    perl_replacement(flt["Replace"]),
                    key,
                    count=1,
                    timeout=REGEX_TIMEOUT_SECONDS,
                )
            except TimeoutError as exc:
                raise MappingError(
                    f"regex timed out after {REGEX_TIMEOUT_SECONDS}s (pattern {flt['Search']!r})"
                ) from exc
            except (regex.error, IndexError) as exc:
                raise MappingError(f"Invalid RegExFilter replacement {flt['Replace']!r}") from exc
            if new_key == key:
                break  # Perl `next KEY`
            data[new_key] = data.pop(key, None)  # Perl delete on a missing key yields undef


# ------------------------------------------------------------------------ mapper


class XsltMapper:
    """``Mapping::XSLT``: XSLT 1.0 transformation with regex pre/post filters."""

    def __init__(self, config: dict[str, Any] | None, *, huge_xml: bool = False) -> None:
        if config is not None and (not isinstance(config, dict) or not config):
            raise MappingConfigError(
                "Got MappingConfig with Data, but Data is no hash ref with content!"
            )
        self._config: dict[str, Any] = config or {}
        self._huge = huge_xml
        self._pre = _filters_check("PreRegExFilter", self._config.get("PreRegExFilter"))
        self._post = _filters_check("PostRegExFilter", self._config.get("PostRegExFilter"))
        self._xslt: Any = None
        self._lock = threading.Lock()

    def _stylesheet(self) -> Any:
        if self._xslt is not None:
            return self._xslt
        with self._lock:
            if self._xslt is not None:
                return self._xslt
            template = prepare_template(perl_str(self._config["Template"]))
            try:
                doc = etree.fromstring(
                    template.encode("utf-8"), _parser(self._huge, with_resolver=True)
                ).getroottree()
            except (etree.XMLSyntaxError, ValueError) as exc:
                raise MappingError("Could not load configured XSLT template") from exc
            try:
                self._xslt = etree.XSLT(doc, access_control=etree.XSLTAccessControl.DENY_ALL)
            except MappingError:
                raise  # resolver refusal: readable "External file ... is not allowed in Tiqora"
            except (etree.XSLTParseError, etree.XMLSyntaxError, etree.LxmlError) as exc:
                if isinstance(exc.__cause__, MappingError):
                    raise exc.__cause__ from exc
                raise MappingError("Could not parse configured XSLT template") from exc
            return self._xslt

    def map(self, data: Any, data_include: DataInclude | None = None) -> Any:
        if data is not None and not isinstance(data, (dict, list)):
            raise MappingError(
                "Got Data but it is not a hash or array ref in Mapping XSLT backend!"
            )
        if data_include is not None and not (isinstance(data_include, dict) and data_include):
            raise MappingError(_INCLUDE_NOT_HASH)
        if not data:
            return {}

        config = self._config
        if not perl_truthy(config.get("Template")):
            return data

        stylesheet = self._stylesheet()

        include = config.get("DataInclude")
        if data_include and isinstance(include, list) and include:
            if not isinstance(data, dict):
                raise MappingError("Could not merge DataInclude into non-hash Data")
            merged = copy.deepcopy(data)
            for name in include:
                # XSLT.pm:211 `next if !$Param{DataInclude}->{$Module}` ({} true, "0" false)
                if perl_truthy(data_include.get(name)):
                    merged.setdefault("DataInclude", {})[name] = copy.deepcopy(data_include[name])
            data = merged
        elif self._pre:
            data = copy.deepcopy(data)  # filters work in place; never touch the caller's data

        if self._pre:
            regex_recursion(data, self._pre)

        xml_pre = xml_out(data)
        try:
            source = etree.fromstring(xml_pre.encode("utf-8"), _parser(self._huge)).getroottree()
        except (etree.XMLSyntaxError, ValueError) as exc:
            raise MappingError("Could not load data after conversion from Perl to XML") from exc

        try:
            result = stylesheet(source)
            xml_post = bytes(result)
        except MappingError:
            raise
        except (etree.LxmlError, RecursionError, MemoryError) as exc:
            raise MappingError("Could not map data") from exc
        if len(xml_post) > MAX_OUTPUT_BYTES:
            raise MappingError("Could not map data")
        if not xml_post:
            raise MappingError("Could not write mapped data")

        force = config.get("ForceArray")
        force_array: frozenset[str] | bool = (
            frozenset(perl_str(force).split()) if perl_truthy(force) else False
        )
        mapped = xml_in(
            xml_post,
            force_array=force_array,
            keep_attributes=perl_truthy(config.get("KeepAttributes")),
            huge_tree=self._huge,
        )
        if self._post:
            regex_recursion(mapped, self._post)
        return mapped
