"""qa-audit doctor: is everything needed here? (docs/qa-audit.md §2, §13)

Exit 0 when every required item is ok (Python, PyYAML, Playwright, at least one browser, bundled axe-core, a
writable output folder); 1 otherwise. Optional items are reported but never fail.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import tempfile

from .core.console import Console, symbols


def _browser_dirs() -> list[str]:
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        return [env]
    home = os.path.expanduser("~")
    return [os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
            os.path.join(home, ".cache", "ms-playwright"),
            os.path.join(home, "Library", "Caches", "ms-playwright")]


def check_engines(engines) -> list[tuple[str, bool, str]]:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    from .browser.session import FIREFOX_PREFS, launch_hint

    out = []
    with sync_playwright() as p:
        for engine in engines:
            try:
                kw = {"firefox_user_prefs": FIREFOX_PREFS} if engine == "firefox" else {}
                b = getattr(p, engine).launch(timeout=60000, **kw)
                version = b.version
                page = b.new_page()
                # the browser's own cryptography: when it is broken (e.g. blocked by Windows application control),
                # no HTTPS page loads at all (docs/qa-audit.md §13)
                page.set_content("<script>try { crypto.getRandomValues(new Uint8Array(1)); document.title = 'ok'; }"
                                 " catch (e) { document.title = 'broken: ' + e.name; }</script>")
                crypto = page.title()
                b.close()
                if crypto == "ok":
                    out.append((engine, True, version))
                else:
                    detail = (f"{version} starts, but its cryptography is {crypto}: HTTPS pages will not load "
                              "(Windows application control?); use another engine")
                    out.append((engine, False, detail))
            except PWError as e:
                out.append((engine, False, launch_hint(engine, str(e))))
    return out


def run(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="qa-audit doctor", description="Check the environment.")
    p.add_argument("--output", default="qa-output", help="output root to check (default: ./qa-output)")
    p.add_argument("--no-color", action="store_true")
    a = p.parse_args(argv)
    c = Console(color=False if a.no_color else None)
    sym = symbols(c.stream)
    rows: list[tuple[str, str, str, bool]] = []     # (status ok|fail|warn, item, detail, required)

    def add(ok: bool, item: str, detail: str, required: bool = True):
        rows.append(("ok" if ok else ("fail" if required else "warn"), item, detail, required))

    add(sys.version_info >= (3, 12), "Python >= 3.12", sys.version.split()[0])
    try:
        import yaml
        add(True, "PyYAML", yaml.__version__)
    except ImportError:
        add(False, "PyYAML", "pip install -e .")
    try:
        from importlib.metadata import version
        pw_version = version("playwright")
        add(True, "Playwright", pw_version)
        engines = check_engines(("chromium", "firefox", "webkit"))
        for engine, ok, detail in engines:
            add(ok, f"browser {engine}", detail, required=False)
        add(any(ok for _, ok, _ in engines), "at least one browser", "chromium, firefox or webkit")
    except ImportError:
        add(False, "Playwright", "pip install -e . && python -m playwright install chromium")
    ffmpeg = any(glob.glob(os.path.join(d, "ffmpeg-*")) for d in _browser_dirs() if d)
    add(ffmpeg, "ffmpeg (only for --video)", "found" if ffmpeg else "python -m playwright install ffmpeg",
        required=False)
    try:
        from .modules.accessibility import axe
        ok, detail = axe.verify_bundle()
        add(ok, "axe-core bundle", detail)
    except ImportError:
        add(True, "axe-core bundle", "accessibility module not installed")
    try:
        os.makedirs(a.output, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=a.output):
            pass
        add(True, "output folder writable", os.path.abspath(a.output))
    except OSError as e:
        add(False, "output folder writable", f"{os.path.abspath(a.output)}: {e}")
    try:
        import PIL
        add(True, "Pillow (visual module)", PIL.__version__, required=False)
    except ImportError:
        add(False, "Pillow (visual module)", "pip install -e .[visual]", required=False)

    c.print("qa-audit doctor", "bold")
    marks = {"ok": (sym["ok"], "green"), "fail": (sym["fail"], "red"), "warn": (sym["warn"], "yellow")}
    width = max(len(r[1]) for r in rows) + 2
    for status, item, detail, _ in rows:
        mark, color = marks[status]
        c.print(f"  {c.style(mark, color)} {item.ljust(width)}{detail}")
    failed = [r for r in rows if r[0] == "fail"]
    c.print()
    c.print("Ready." if not failed else f"{len(failed)} required item(s) missing.", "green" if not failed else "red")
    return 1 if failed else 0
