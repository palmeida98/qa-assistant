"""Module discovery (docs/qa-audit.md §10): built-in packages under qa_audit.modules and the entry point group
"qa_audit.modules". Adding a module never needs a change here."""

from __future__ import annotations

import importlib
import pkgutil
import re
from importlib.metadata import entry_points

from .config import ConfigError
from .module import AuditModule

ENTRY_POINT_GROUP = "qa_audit.modules"
NAME = re.compile(r"^[a-z][a-z0-9-]*$")
# shown when someone asks for them before they exist (plan phase 8)
PLANNED: dict[str, str] = {}


class RegistryError(RuntimeError):
    pass


def _as_instance(obj, origin: str) -> AuditModule:
    if isinstance(obj, type) and issubclass(obj, AuditModule):
        obj = obj()
    if not isinstance(obj, AuditModule):
        raise RegistryError(f"{origin}: expected an AuditModule subclass or instance, got {type(obj).__name__}")
    if not NAME.match(obj.name or ""):
        raise RegistryError(f"{origin}: invalid module name {obj.name!r} (lowercase letters, digits, '-')")
    if not obj.title:
        raise RegistryError(f"{origin}: module {obj.name!r} has no title")
    return obj


def discover(package: str = "qa_audit.modules", use_entry_points: bool = True) -> dict[str, AuditModule]:
    found: dict[str, AuditModule] = {}

    def add(mod: AuditModule, origin: str):
        if mod.name in found:
            raise RegistryError(f"{origin}: module name {mod.name!r} is already registered")
        found[mod.name] = mod

    pkg = importlib.import_module(package)
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if not info.ispkg:
            continue
        origin = f"{package}.{info.name}.module"
        m = importlib.import_module(origin)
        if not hasattr(m, "MODULE"):
            raise RegistryError(f"{origin}: no MODULE defined")
        add(_as_instance(m.MODULE, origin), origin)
    if use_entry_points:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            add(_as_instance(ep.load(), f"entry point {ep.name}"), f"entry point {ep.name}")
    return found


def select(types: str, available: dict[str, AuditModule]) -> list[AuditModule]:
    """Modules for --type ('full' or a comma list). ConfigError for unknown or not-yet-available names."""
    names = [t.strip().lower() for t in (types or "full").split(",") if t.strip()]
    if not names or names == ["full"]:
        return sorted(available.values(), key=lambda m: m.order)
    if "full" in names:
        raise ConfigError("--type full cannot be combined with other types")
    chosen, problems = [], []
    for n in names:
        if n in available:
            if available[n] not in chosen:
                chosen.append(available[n])
        elif n in PLANNED:
            problems.append(f"'{n}' is not available yet (planned in task {PLANNED[n]})")
        else:
            problems.append(f"unknown type '{n}'")
    if problems:
        raise ConfigError("; ".join(problems) + f". Available: {', '.join(available) or 'none'}, full")
    return sorted(chosen, key=lambda m: m.order)
