"""qa-audit command line (docs/qa-audit.md §2).

Exit codes: 0 ok · 1 problems at or above --fail-on · 2 usage or configuration · 3 target unreachable ·
4 browser could not start.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .core import config as config_mod
from .core.config import ConfigError
from .core.console import Console, setup_streams
from .core.findings import severity_rank

EPILOG = """examples:
  qa-audit https://example.com
  qa-audit https://example.com --type accessibility --browser firefox
  qa-audit https://example.com --max-pages 5 --report html,json --cleanup
  qa-audit doctor
  qa-audit version
  qa-audit baselines --delete example.com

Only audit sites you own or are authorized to test. Specification: docs/qa-audit.md"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qa-audit", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG,
        description="Automated QA audit of a website. Other commands: 'qa-audit doctor', 'qa-audit version', "
                    "'qa-audit baselines', 'qa-audit login', 'qa-audit sessions'.")
    p.add_argument("url", help="target URL (http or https)")
    p.add_argument("--type", default=None, metavar="LIST",
                   help="comma-separated modules, or 'full' (default: full)")
    p.add_argument("--browser", choices=config_mod.ENGINES, default=None, help="browser engine (default: chromium)")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--viewport", default=None, metavar="WxH", help="viewport size (default: 1440x900)")
    p.add_argument("--device", default=None, metavar="NAME", help="Playwright device, e.g. 'iPhone 15'")
    p.add_argument("--max-pages", type=int, default=None, metavar="N", help="pages to visit (default: 20)")
    p.add_argument("--depth", type=int, default=None, metavar="N", help="link depth from the start URL (default: 2)")
    p.add_argument("--delay-ms", type=int, default=None, metavar="N", help="pause between pages (default: 500)")
    p.add_argument("--exclude", action="append", default=None, metavar="REGEX",
                   help="extra URL pattern never visited (repeatable)")
    p.add_argument("--screenshots", nargs="?", const="all", default=None, choices=config_mod.SCREENSHOT_MODES,
                   help="off | on-problem | all (default: on-problem; the flag alone means all)")
    p.add_argument("--video", action="store_true", help="record video (sensitive: stores page content)")
    p.add_argument("--trace", default=None, choices=config_mod.TRACE_MODES,
                   help="Playwright trace (sensitive: stores page content and cookies; default: off)")
    p.add_argument("--report", default=None, metavar="LIST", help="html,json,csv,md (default: all)")
    p.add_argument("--output", default=None, metavar="DIR", help="output root (default: ./qa-output)")
    p.add_argument("--cleanup", action="store_true", help="delete the run folder after the summary")
    p.add_argument("--config", default=None, metavar="FILE", help="YAML configuration file")
    p.add_argument("--fail-on", default=None, choices=config_mod.FAIL_ON,
                   help="exit 1 on problems at or above this severity (default: high)")
    p.add_argument("--baseline-dir", default=None, metavar="DIR",
                   help="visual baselines folder (default: ~/.qa-audit/baselines; never inside the output)")
    p.add_argument("--update-baseline", action="store_true",
                   help="replace the visual baselines with this run's screenshots (after reviewing a change)")
    p.add_argument("--allow-submit", action="store_true",
                   help="submit the forms listed under modules.functional.forms (never forms found by discovery)")
    p.add_argument("--ignore-https-errors", action="store_true",
                   help="accept a certificate the browser rejects (e.g. self-signed staging); trust is then not judged")
    p.add_argument("--block-third-party", action="store_true",
                   help="block requests outside the target origin (the audit then differs from what visitors see)")
    p.add_argument("--no-robots", action="store_true", help="ignore robots.txt")
    p.add_argument("--no-color", action="store_true", help="plain output (also NO_COLOR)")
    return p


def overrides_from_args(a: argparse.Namespace) -> dict:
    o: dict = {"browser": {}, "crawl": {}, "evidence": {}, "output": {}, "network": {}, "safety": {},
               "modules": {}}
    if a.browser:
        o["browser"]["engine"] = a.browser
    if a.headed:
        o["browser"]["headless"] = False
    if a.viewport:
        o["browser"]["viewport"] = config_mod.parse_viewport(a.viewport)
    if a.device:
        o["browser"]["device"] = a.device
    for arg, key in ((a.max_pages, "max_pages"), (a.depth, "max_depth"), (a.delay_ms, "delay_ms")):
        if arg is not None:
            o["crawl"][key] = arg
    if a.no_robots:
        o["crawl"]["respect_robots"] = False
    if a.screenshots:
        o["evidence"]["screenshots"] = a.screenshots
    if a.video:
        o["evidence"]["video"] = True
    if a.trace:
        o["evidence"]["trace"] = a.trace
    if a.report:
        o["output"]["formats"] = [x.strip().lower() for x in a.report.split(",") if x.strip()]
    if a.output:
        o["output"]["directory"] = a.output
    if a.cleanup:
        o["output"]["cleanup"] = True
    visual = {}
    if a.baseline_dir:
        visual["baseline_dir"] = a.baseline_dir
    if a.update_baseline:
        visual["update_baseline"] = True
    if visual:
        o["modules"]["visual"] = visual
    if a.allow_submit:
        o["safety"]["allow_submit"] = True
    if a.ignore_https_errors:
        o["browser"]["ignore_https_errors"] = True
    if a.block_third_party:
        o["network"]["block_third_party"] = True
    if a.fail_on:
        o["fail_on"] = a.fail_on
    return {k: v for k, v in o.items() if v != {}}


def configure(a: argparse.Namespace):
    """(target, cfg, modules) or ConfigError / RegistryError."""
    from .core import registry

    target = config_mod.validate_url(a.url)
    available = registry.discover()
    cfg = config_mod.build(a.config, overrides_from_args(a),
                           module_defaults={n: m.default_config() for n, m in available.items()})
    if a.exclude:
        cfg["crawl"]["exclude"] = list(cfg["crawl"]["exclude"]) + a.exclude
        config_mod.validate(cfg)
    modules = registry.select(a.type or "full", available)
    from .core import auth
    problems = [p for m in modules for p in m.validate_config(cfg["modules"].get(m.name) or {})]
    problems += auth.validate(cfg, target=target)
    if a.type not in (None, "full"):
        # asked for by name: a module that cannot run here is an error, not a silent skip
        for m in modules:
            ok, reason = m.applicable(None)
            if not ok:
                problems.append(f"{m.name}: {reason}")
    if problems:
        raise ConfigError("; ".join(problems))
    return target, cfg, modules


def exit_code(result, fail_on: str) -> int:
    if fail_on == "none":
        return 0
    limit = severity_rank(fail_on)
    return 1 if any(f.status == "problem" and severity_rank(f.severity) <= limit for f in result.findings) else 0


def audit(argv: list[str], console: Console | None = None) -> int:
    from .core.registry import RegistryError

    parser = build_parser()
    a = parser.parse_args(argv)
    console = console or Console(color=False if a.no_color else None)
    err = Console(stream=sys.stderr, color=console.color)
    try:
        target, cfg, modules = configure(a)
    except (ConfigError, RegistryError) as e:
        err.print(f"qa-audit: error: {e}", "red")
        return 2
    if a.type in (None, "full"):
        kept = []
        for m in modules:
            ok, reason = m.applicable(None)
            if ok:
                kept.append(m)
            else:
                console.print(f"skipping {m.title}: {reason}", "dim")
        modules = kept

    from .browser.discovery import TargetUnreachable
    from .browser.session import BrowserError
    from .core import runner
    from .core.auth import AuthError
    from .reports import terminal, write_all

    steps = ["Discovery"] + [m.title for m in modules] + ["Report"]
    prog = terminal.Progress(console, steps)
    prog.banner(target, cfg, modules)
    state = {"i": 1}

    def progress(event, **d):
        if event == "auth_start":
            console.print(f"      logging in ({d['method']}) ...", "dim")
        elif event == "auth_manual":
            console.print(f"      Log in in the browser window ({d['url']}); waiting up to {d['timeout_s']} s ...",
                          "yellow")
        elif event == "auth_done":
            console.print(f"      {d['detail']}", "dim")
        elif event == "page":
            prog.page(d["url"], d["n"])
        elif event == "discovery_done":
            prog.step(1, True, f"{d['pages']} pages, {d['skipped']} links skipped")
        elif event == "module_done":
            state["i"] += 1
            prog.step(state["i"], True, f"{d['findings']} findings")

    try:
        result = runner.run(target, cfg, modules, progress=progress)
    except ConfigError as e:
        err.print(f"qa-audit: error: {e}", "red")
        return 2
    except AuthError as e:
        prog.step(1, False, "login failed")
        err.print(f"qa-audit: login failed: {e}", "red")
        return 3
    except TargetUnreachable as e:
        prog.step(1, False, "target unreachable")
        err.print(f"qa-audit: {e}", "red")
        return 3
    except BrowserError as e:
        err.print(f"qa-audit: the browser could not start: {e}", "red")
        err.print("run 'qa-audit doctor' for details", "dim")
        return 4
    paths = write_all(result, cfg["output"]["formats"])
    prog.step(len(steps), True, ", ".join(paths) if paths else "terminal only")
    cleaned = False
    if cfg["output"]["cleanup"]:
        result.run.cleanup()
        cleaned = True
    terminal.summary(console, result, paths, cleaned, cfg["fail_on"])
    return exit_code(result, cfg["fail_on"])


def baselines(argv: list[str]) -> int:
    """List or delete the stored visual baselines (docs/qa-audit.md §19)."""
    from .modules.visual import compare

    p = argparse.ArgumentParser(prog="qa-audit baselines", description="List or delete visual baselines.")
    p.add_argument("--baseline-dir", default=None, metavar="DIR", help="default: ~/.qa-audit/baselines")
    p.add_argument("--delete", default=None, metavar="HOST", help="a host as listed, or 'all'")
    a = p.parse_args(argv)
    root = compare.root(a.baseline_dir)
    if a.delete:
        removed = compare.delete_baselines(root, a.delete)
        print(f"deleted: {', '.join(removed)}" if removed else f"nothing to delete for {a.delete!r} in {root}")
        return 0 if removed else 1
    rows = compare.list_baselines(root)
    print(f"visual baselines in {root}")
    for host, files, size in rows:
        print(f"  {host:<40} {files:>5} screenshot(s) {size / 1024 / 1024:>8.1f} MB")
    if not rows:
        print("  (none)")
    return 0


def login(argv: list[str], console: Console | None = None) -> int:
    """Log in once and save the session for auth.method storage-state (docs/qa-audit.md §20.1)."""
    import copy

    from .browser.session import BrowserError, Session
    from .core import auth

    p = argparse.ArgumentParser(prog="qa-audit login", description="Log in once and save the browser session.")
    p.add_argument("url", help="target URL (http or https)")
    p.add_argument("--config", required=True, metavar="FILE", help="YAML configuration with auth.method storage-state")
    p.add_argument("--browser", choices=config_mod.ENGINES, default=None, help="browser engine (default: chromium)")
    p.add_argument("--no-color", action="store_true", help="plain output (also NO_COLOR)")
    a = p.parse_args(argv)
    console = console or Console(color=False if a.no_color else None)
    err = Console(stream=sys.stderr, color=console.color)
    try:
        target = config_mod.validate_url(a.url)
        cfg = config_mod.build(a.config, {"browser": {"engine": a.browser}} if a.browser else {})
        problems = auth.validate(cfg, target=target, mode="login")
        if problems:
            raise ConfigError("; ".join(problems))
    except ConfigError as e:
        err.print(f"qa-audit: error: {e}", "red")
        return 2
    path = auth.state_path(cfg, target)
    # the session is created with the login method itself (form or manual); nothing is recorded or audited
    run_cfg = copy.deepcopy(cfg)
    run_cfg["auth"]["method"] = auth.login_method(cfg["auth"])
    run_cfg["evidence"].update({"video": False, "trace": "off"})

    def progress(event, **d):
        if event == "auth_manual":
            console.print(f"Log in in the browser window ({d['url']}); waiting up to {d['timeout_s']} s ...", "yellow")

    try:
        with Session(run_cfg, None, target) as session:
            done = auth.login(session, run_cfg, target, progress=progress)
            auth.save_state(session.context, path)
    except BrowserError as e:
        err.print(f"qa-audit: browser could not start: {e} (run qa-audit doctor)", "red")
        return 4
    except auth.AuthError as e:
        err.print(f"qa-audit: login failed: {e}", "red")
        return 3
    console.print(f"{done}; session saved to {path}", "green")
    console.print("It holds live cookies: keep it out of repositories; 'qa-audit sessions --delete HOST' removes it.")
    return 0


def sessions(argv: list[str]) -> int:
    """List or delete the saved sessions (docs/qa-audit.md §20.1)."""
    from .core import auth

    p = argparse.ArgumentParser(prog="qa-audit sessions", description="List or delete saved login sessions.")
    p.add_argument("--sessions-dir", default=None, metavar="DIR", help="default: ~/.qa-audit/sessions")
    p.add_argument("--delete", default=None, metavar="HOST", help="a host as listed, or 'all'")
    a = p.parse_args(argv)
    root = auth.sessions_root(a.sessions_dir)
    if a.delete:
        removed = auth.delete_sessions(root, a.delete)
        print(f"deleted: {', '.join(removed)}" if removed else f"nothing to delete for {a.delete!r} in {root}")
        return 0 if removed else 1
    rows = auth.list_sessions(root)
    print(f"saved sessions in {root}")
    for host, age, size in rows:
        print(f"  {host:<40} {age:>8.1f} h old {size / 1024:>8.1f} KB")
    if not rows:
        print("  (none)")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_streams()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["version"], ["--version"]):
        print(f"qa-audit {__version__}")
        return 0
    if argv[:1] == ["baselines"]:
        return baselines(argv[1:])
    if argv[:1] == ["login"]:
        return login(argv[1:])
    if argv[:1] == ["sessions"]:
        return sessions(argv[1:])
    if argv[:1] == ["doctor"]:
        from .doctor import run as doctor
        return doctor(argv[1:])
    return audit(argv)


if __name__ == "__main__":
    sys.exit(main())
