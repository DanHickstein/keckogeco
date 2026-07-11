"""KTL keyword schema and registry.

The schema (``comb/schema/keywords.toml``) is the 77-keyword set from the
old ``LFC.xml.sin`` agreed with Keck, enriched with units/limits. The
registry replaces the old convention of one same-named method per keyword
with ``value=None`` meaning read and return-0-to-commit writes
(``KeckLFC.py`` ``__getitem__``/``__setitem__``): here getters/setters are
explicit callables, writes validate against the schema first and raise on
failure, and the last-read values live in a lock-protected cache.
"""

from __future__ import annotations

import threading
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources

__all__ = ["KeywordRegistry", "KeywordSpec", "KeywordValue", "load_schema"]


class KeywordError(Exception):
    """Unknown keyword, bad value, or missing binding."""


@dataclass(frozen=True)
class KeywordSpec:
    """One keyword's definition from the schema."""

    name: str
    type: str  # boolean | integer | double | string | enumerated | double array
    writable: bool = False
    help: str = ""
    units: str = ""
    min: float | None = None
    max: float | None = None
    enum: dict[str, str] = field(default_factory=dict)
    format: str = ""

    def convert(self, raw) -> object:
        """Convert an incoming (usually string) value to the keyword's type.

        Replaces the old ``convert_type`` switch; KTL and HTTP both deliver
        strings.
        """
        if self.type == "boolean":
            text = str(raw).strip().casefold()
            if text in ("1", "on", "true", "yes"):
                return True
            if text in ("0", "off", "false", "no"):
                return False
            raise KeywordError(f"{self.name}: {raw!r} is not a boolean")
        if self.type == "integer":
            return int(str(raw).strip())
        if self.type == "double":
            return float(raw)
        if self.type == "enumerated":
            text = str(raw).strip()
            if text in self.enum:
                return int(text)
            # accept the enum label too ("ready" -> 1)
            for key, label in self.enum.items():
                if text.casefold() == label.casefold():
                    return int(key)
            raise KeywordError(f"{self.name}: {raw!r} not in enum {dict(self.enum)}")
        if self.type == "double array":
            if isinstance(raw, list | tuple):
                return [float(v) for v in raw]
            return [float(v) for v in str(raw).split()]
        return str(raw)

    def validate(self, value) -> None:
        if self.type in ("double", "integer"):
            if self.min is not None and value < self.min:
                raise KeywordError(f"{self.name}: {value} below minimum {self.min} {self.units}")
            if self.max is not None and value > self.max:
                raise KeywordError(f"{self.name}: {value} above maximum {self.max} {self.units}")


@dataclass(frozen=True)
class KeywordValue:
    value: object
    timestamp: float


def load_schema() -> dict[str, KeywordSpec]:
    """Load the packaged keyword schema."""
    text = (resources.files("keckogeco.comb") / "schema" / "keywords.toml").read_text()
    data = tomllib.loads(text)
    schema = {}
    for name, block in data.items():
        schema[name] = KeywordSpec(
            name=name,
            type=block.get("type", "string"),
            writable=bool(block.get("writable", False)),
            help=block.get("help", ""),
            units=block.get("units", ""),
            min=block.get("min"),
            max=block.get("max"),
            enum=dict(block.get("enum", {})),
            format=block.get("format", ""),
        )
    return schema


class KeywordRegistry:
    """Maps KTL keyword names to getter/setter callables with a value cache.

    Bind with :meth:`bind` (programmatic, used by the controller so
    bindings can depend on which devices are configured) or the
    :meth:`getter`/:meth:`setter` decorators.
    """

    def __init__(self, schema: dict[str, KeywordSpec] | None = None):
        self.schema = schema if schema is not None else load_schema()
        self._getters: dict[str, Callable[[], object]] = {}
        self._setters: dict[str, Callable[[object], None]] = {}
        self._cache: dict[str, KeywordValue] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- binding

    def _spec(self, name: str) -> KeywordSpec:
        spec = self.schema.get(name)
        if spec is None:
            raise KeywordError(f"Unknown keyword {name!r} (not in schema)")
        return spec

    def bind(
        self,
        name: str,
        getter: Callable[[], object] | None = None,
        setter: Callable[[object], None] | None = None,
    ) -> None:
        spec = self._spec(name)
        if getter is not None:
            self._getters[name] = getter
        if setter is not None:
            if not spec.writable:
                raise KeywordError(f"{name} is read-only in the schema")
            self._setters[name] = setter

    def getter(self, name: str):
        def decorate(func):
            self.bind(name, getter=func)
            return func

        return decorate

    def setter(self, name: str):
        def decorate(func):
            self.bind(name, setter=func)
            return func

        return decorate

    # ----------------------------------------------------------- accessors

    def read(self, name: str) -> KeywordValue:
        """Read a keyword from hardware, updating the cache."""
        self._spec(name)
        getter = self._getters.get(name)
        if getter is None:
            raise KeywordError(f"{name} has no getter bound (not implemented yet)")
        result = KeywordValue(getter(), time.time())
        with self._lock:
            self._cache[name] = result
        return result

    def write(self, name: str, raw) -> object:
        """Validate, convert, and write a keyword. Returns the converted
        value. Raises on any failure (no silent return codes)."""
        spec = self._spec(name)
        if not spec.writable:
            raise KeywordError(f"{name} is read-only")
        setter = self._setters.get(name)
        if setter is None:
            raise KeywordError(f"{name} has no setter bound (not implemented yet)")
        value = spec.convert(raw)
        spec.validate(value)
        setter(value)
        with self._lock:
            self._cache[name] = KeywordValue(value, time.time())
        return value

    def snapshot(self) -> dict[str, KeywordValue]:
        """Last-read values (no hardware I/O)."""
        with self._lock:
            return dict(self._cache)

    def poke(self, name: str, value) -> None:
        """Update the cache without hardware I/O (heartbeats, monitors)."""
        self._spec(name)
        with self._lock:
            self._cache[name] = KeywordValue(value, time.time())

    # ------------------------------------------------------------- reports

    @property
    def bound(self) -> set[str]:
        return set(self._getters) | set(self._setters)

    def missing_getters(self) -> set[str]:
        return set(self.schema) - set(self._getters)

    def missing_setters(self) -> set[str]:
        return {
            name
            for name, spec in self.schema.items()
            if spec.writable and name not in self._setters
        }
