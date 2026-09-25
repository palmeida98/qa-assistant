"""Configuration (docs/qa-audit.md §4).

Precedence: built-in defaults < each module's config.yaml < --config file < command-line options.
A configuration file never holds credentials: a secret-looking key with a literal value is refused.
"""

from __future__ import annotations

import copy
import os
import re
from urllib.parse import urlsplit

import yaml

ENGINES = ("chromium", "firefox", "webkit")
SCREENSHOT_MODES = ("off", "on-problem", "all")
TRACE_MODES = ("off", "on", "retain-on-problem")
REPORT_FORMATS = ("html", "json", "csv", "md")
FAIL_ON = ("critical", "high", "medium", "low", "info", "none")

DEFAULTS: dict = {
    "browser": {
        "engine": "chromium",
        "headless": True,
        "viewport": {"width": 1440, "height": 900},
        "device": None,
        "timeout_ms": 30000,
        "ignore_https_errors": False,
    },
    "crawl": {
        "max_pages": 20,
        "max_depth": 2,
        "delay_ms": 500,
        "respect_robots": True,
        "exclude": [],
    },
    "network": {"block_third_party": False},
    "evidence": {
        "screenshots": "on-problem",
        "max_element_screenshots": 5,
        "video": False,
        "trace": "off",
    },
    "output": {
        "directory": "qa-output",
        "formats": list(REPORT_FORMATS),
        "cleanup": False,
    },
    "fail_on": "high",
    "modules": {},
}

# a key that names a secret; "<name>_env" (the name of an environment variable) is allowed
SECRET_KEY = re.compile(r"(^|_)pass(word|wd)?$|secret|token|api[_-]?key|apikey|authorization|cookie|credential",
                        re.IGNORECASE)


class ConfigError(ValueError):
    """Invalid configuration or usage (exit code 2)."""


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def find_literal_secrets(obj, path: str = "") -> list[str]:
    """Paths of secret-looking keys that carry a literal value (docs/qa-audit.md §4)."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            key = str(k)
            literal = v not in (None, "", False) and not isinstance(v, (dict, list))
            if SECRET_KEY.search(key) and not key.lower().endswith("_env") and literal:
                found.append(p)
            found.extend(find_literal_secrets(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(find_literal_secrets(v, f"{path}[{i}]"))
    return found


def load_file(path: str) -> dict:
    if not os.path.isfile(path):
        raise ConfigError(f"configuration file not found: {path}")
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the configuration must be a mapping")
    secrets = find_literal_secrets(data)
    if secrets:
        raise ConfigError(f"{path}: credentials are not allowed in configuration files ({', '.join(secrets)}); "
                          "name an environment variable instead (e.g. password_env: SHOP_ADMIN_PASSWORD)")
    return data


def validate_url(url: str) -> str:
    """The target URL, normalized (no fragment). ConfigError if it is not http(s) with a host."""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"invalid target URL {url!r}: expected http:// or https:// with a host")
    return parts._replace(fragment="").geturl()


def parse_viewport(text: str) -> dict:
    m = re.fullmatch(r"(\d{2,5})x(\d{2,5})", (text or "").strip().lower())
    if not m:
        raise ConfigError(f"invalid viewport {text!r}: expected WIDTHxHEIGHT, e.g. 1440x900")
    return {"width": int(m.group(1)), "height": int(m.group(2))}


def build(file_path: str | None = None, overrides: dict | None = None, module_defaults: dict | None = None) -> dict:
    """The effective configuration. module_defaults: {module name: its config.yaml}."""
    cfg = copy.deepcopy(DEFAULTS)
    cfg["modules"] = deep_merge(cfg["modules"], module_defaults or {})
    if file_path:
        cfg = deep_merge(cfg, load_file(file_path))
    cfg = deep_merge(cfg, overrides or {})
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    problems = []
    b, c, e, o = cfg["browser"], cfg["crawl"], cfg["evidence"], cfg["output"]
    if b.get("engine") not in ENGINES:
        problems.append(f"browser.engine must be one of {', '.join(ENGINES)}")
    vp = b.get("viewport") or {}
    if not (isinstance(vp.get("width"), int) and isinstance(vp.get("height"), int)):
        problems.append("browser.viewport needs integer width and height")
    for key, lo in (("max_pages", 1), ("max_depth", 0), ("delay_ms", 0)):
        if not isinstance(c.get(key), int) or c[key] < lo:
            problems.append(f"crawl.{key} must be an integer >= {lo}")
    if not isinstance(c.get("exclude"), list):
        problems.append("crawl.exclude must be a list of regular expressions")
    else:
        for pat in c["exclude"]:
            try:
                re.compile(pat)
            except re.error as ex:
                problems.append(f"crawl.exclude: invalid regular expression {pat!r}: {ex}")
    if e.get("screenshots") not in SCREENSHOT_MODES:
        problems.append(f"evidence.screenshots must be one of {', '.join(SCREENSHOT_MODES)}")
    if str(e.get("trace")) not in TRACE_MODES:
        problems.append(f"evidence.trace must be one of {', '.join(TRACE_MODES)}")
    bad = [f for f in o.get("formats") or [] if f not in REPORT_FORMATS]
    if bad:
        problems.append(f"output.formats: unknown {', '.join(bad)} (available: {', '.join(REPORT_FORMATS)})")
    if cfg.get("fail_on") not in FAIL_ON:
        problems.append(f"fail_on must be one of {', '.join(FAIL_ON)}")
    if problems:
        raise ConfigError("; ".join(problems))

