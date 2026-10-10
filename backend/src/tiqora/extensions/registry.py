"""Internal extension registry: typed extension points and their items.

Core features (and, later, plugins) register items against an
:class:`ExtensionPoint`. Keys are unique per point regardless of owner, and the
registry is frozen after startup so a broken registration fails at boot.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class DuplicateExtensionError(ValueError):
    """An item with the same key is already registered on the point."""


class RegistryFrozenError(RuntimeError):
    """The registry no longer accepts additions."""


@dataclass(frozen=True, slots=True)
class ExtensionPoint[T]:
    key: str
    item_key: Callable[[T], str]
    description: str
    sdk_stable: bool = False


@dataclass(frozen=True, slots=True)
class Registration[T]:
    point_key: str
    key: str
    item: T
    owner: str


class Registry:
    def __init__(self) -> None:
        self._points: dict[str, ExtensionPoint[Any]] = {}
        self._items: dict[str, dict[str, Registration[Any]]] = {}
        self._frozen = False

    def add[T](self, point: ExtensionPoint[T], item: T, *, owner: str = "core") -> None:
        if self._frozen:
            raise RegistryFrozenError(f"Registry is frozen; cannot add to {point.key}")
        key = point.item_key(item)
        bucket = self._items.setdefault(point.key, {})
        if key in bucket:
            raise DuplicateExtensionError(
                f"{point.key}: {key!r} already registered by {bucket[key].owner!r}"
            )
        self._points.setdefault(point.key, point)
        bucket[key] = Registration(point_key=point.key, key=key, item=item, owner=owner)

    def get[T](self, point: ExtensionPoint[T], key: str) -> T | None:
        reg = self._items.get(point.key, {}).get(key)
        return None if reg is None else reg.item

    def require[T](self, point: ExtensionPoint[T], key: str) -> T:
        item = self.get(point, key)
        if item is None:
            raise KeyError(f"{point.key}: {key}")
        return item

    def items[T](self, point: ExtensionPoint[T]) -> list[T]:
        return [r.item for r in self._items.get(point.key, {}).values()]

    def registrations[T](self, point: ExtensionPoint[T] | None = None) -> list[Registration[Any]]:
        if point is not None:
            return list(self._items.get(point.key, {}).values())
        return [r for bucket in self._items.values() for r in bucket.values()]

    def points(self) -> list[ExtensionPoint[Any]]:
        return [p for k, p in self._points.items() if self._items.get(k)]

    def replace_contents(self, other: Registry) -> None:
        """Adopt ``other``'s items in place (bootstrap only: late plugin boot, see R-BOOT-RETRY).

        Holders of this registry keep seeing a frozen registry; only the content changes.
        """
        self._points = dict(other._points)
        self._items = {k: dict(v) for k, v in other._items.items()}

    def freeze(self) -> None:
        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen
