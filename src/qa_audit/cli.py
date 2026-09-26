"""qa-audit command line (docs/qa-audit.md §2).

Exit codes: 0 ok · 1 problems at or above --fail-on · 2 usage or configuration · 3 target unreachable ·
4 browser could not start.
"""

from __future__ import annotations

import argparse
import os
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
    p.add_argument("--browser", default=None, metavar="ENGINE",
                   help="chromium, firefox or webkit (default: chromium); a comma-separated list, or 'all', runs one "
                        "audit per engine and compares them")
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
    p.add_argument("--sitemap", action="store_true", help="also queue the pages listed in the site's sitemap")
    p.add_argument("--no-color", action="store_true", help="plain output (also NO_COLOR)")
    return p


def overrides_from_args(a: argparse.Namespace) -> dict:
    o: dict = {"browser": {}, "crawl": {}, "evidence": {}, "output": {}, "network": {}, "safety": {},
               "modules": {}}
    if a.browser:
        o["browser"]["engine"] = config_mod.parse_engines(a.browser)
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
    if a.sitemap:
        o["crawl"]["sitemap"] = True
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
    try:
        several = len(config_mod.engines(cfg)) > 1
    except ValueError:
        several = False                   # already reported by the configuration validation
    if several and (cfg.get("auth") or {}).get("method") == "manual":
        problems.append("auth.method manual cannot run with several engines (the person would log in once per "
                        "engine): save the session once with auth.method storage-state and qa-audit login "
                        "(docs/qa-audit.md §20.1, §21)")
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

    engines = config_mod.engines(cfg)
    if len(engines) == 1:
        cfg["browser"]["engine"] = engines[0]
        return _audit_one(target, cfg, modules, console, err)[0]
    return _audit_many(target, cfg, modules, engines, console, err)


def _audit_one(target, cfg, modules, console, err, run_name: str | None = None):
    """One engine. (exit code, result or None, report paths, result label). With run_name, the run is one engine
    of several (§21): no cleanup and no final summary here."""
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
        result = runner.run(target, cfg, modules, progress=progress, run_name=run_name)
    except ConfigError as e:
        err.print(f"qa-audit: error: {e}", "red")
        return 2, None, {}, f"configuration error: {e}"
    except AuthError as e:
        prog.step(1, False, "login failed")
        err.print(f"qa-audit: login failed: {e}", "red")
        return 3, None, {}, f"login failed: {e}"
    except TargetUnreachable as e:
        prog.step(1, False, "target unreachable")
        err.print(f"qa-audit: {e}", "red")
        return 3, None, {}, f"target unreachable: {e}"
    except BrowserError as e:
        err.print(f"qa-audit: the browser could not start: {e}", "red")
        err.print("run 'qa-audit doctor' for details", "dim")
        return 4, None, {}, f"browser could not start: {e}"
    paths = write_all(result, cfg["output"]["formats"])
    prog.step(len(steps), True, ", ".join(paths) if paths else "terminal only")
    if run_name:
        return exit_code(result, cfg["fail_on"]), result, paths, "audited"
    cleaned = False
    if cfg["output"]["cleanup"]:
        result.run.cleanup()
        cleaned = True
    terminal.summary(console, result, paths, cleaned, cfg["fail_on"])
    return exit_code(result, cfg["fail_on"]), result, paths, "audited"


def _audit_many(target, cfg, modules, engines, console, err) -> int:
    """Several engines: one audit each, one after the other, then the comparison (docs/qa-audit.md §21)."""
    import copy
    import datetime as dt
    import time

    from .core import compatibility
    from .core.evidence import RunFolder
    from .reports import compat_report

    parent = RunFolder(cfg["output"]["directory"])
    started = dt.datetime.now().isoformat(timespec="seconds")
    rows, results, codes = [], {}, {}
    for i, engine in enumerate(engines):
        if i and cfg["crawl"]["delay_ms"]:
            time.sleep(cfg["crawl"]["delay_ms"] / 1000)
        run_cfg = copy.deepcopy(cfg)
        run_cfg["browser"]["engine"] = engine
        run_cfg["output"].update({"directory": parent.path, "cleanup": False})
        console.print(f"\n=== {engine} ({i + 1} of {len(engines)}) ===", "bold")
        code, result, paths, label = _audit_one(target, run_cfg, modules, console, err, run_name=engine)
        codes[engine] = code
        row = {"engine": engine, "folder": engine, "exit_code": code, "result": label, "report": "html" in paths}
        if result is not None:
            results[engine] = result
            row.update(version=result.browser.get("version", ""), summary=result.summary)
        rows.append(row)

    differences = compatibility.compare(results)
    unreachable = [r for r in rows if r["engine"] not in results and codes[r["engine"]] == 3]
    if results and unreachable:
        reached = {r["engine"] for r in rows if codes[r["engine"]] != 3}
        differences.insert(0, compatibility.difference(
            target, "Target reachable in some engines only", compatibility.split(engines, reached),
            "; ".join(f"{r['engine']}: {r['result']}" for r in unreachable)))
    notes = [f"the target was audited {len(results)} time(s), once per engine that ran ({', '.join(results)})"]
    if (cfg.get("auth") or {}).get("method") == "form":
        notes.append(f"auth: the account was logged in {sum(1 for c in codes.values() if c != 4)} time(s), "
                     "once per engine that started")
    comp = {"tool": {"name": "qa-audit", "version": __version__}, "target": target, "started_at": started,
            "engines": rows, "differences": differences, "notes": notes}
    paths = compat_report.write_all(comp, parent.path, cfg["output"]["formats"])

    console.print("")
    for r in rows:
        console.print(f"  {r['engine']:<9} {r['result']}", "green" if r["engine"] in results else "red")
    console.print(f"  {len(differences)} compatibility difference(s) (needs human)")
    for fmt, path in paths.items():
        console.print(f"  {fmt}: {path}", "dim")
    if cfg["output"]["cleanup"]:
        parent.cleanup()
        console.print(f"  run folder removed (--cleanup): {parent.path}", "dim")
    if all(c == 4 for c in codes.values()):
        return 4
    if not results:
        return 3 if any(c == 3 for c in codes.values()) else 2
    return max(exit_code(r, cfg["fail_on"]) for r in results.values())


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


def check(argv: list[str]) -> int:
    """Audit a page another tool has open, in its current state (docs/qa-audit.md §22)."""
    from .core import check as check_mod

    p = argparse.ArgumentParser(prog="qa-audit check", description="Audit a page another tool has open (CDP).")
    p.add_argument("--cdp", required=True, metavar="URL", help="loopback CDP endpoint, e.g. http://127.0.0.1:9222")
    p.add_argument("--page-url", required=True, metavar="REGEX", help="regular expression for the tab's URL")
    p.add_argument("--label", required=True, metavar="NAME", help="name of this state (e.g. checkout-error)")
    p.add_argument("--checks", default="axe", metavar="LIST", help="axe (default), axe,keyboard,reflow")
    p.add_argument("--output", default="./qa-output", metavar="DIR", help="output root (default: ./qa-output)")
    p.add_argument("--fail-on", default="none", choices=config_mod.FAIL_ON,
                   help="exit 1 on problems at or above this severity (default: none)")
    p.add_argument("--redact-env", action="append", default=[], metavar="NAME",
                   help="environment variable whose value is redacted in the output (repeatable)")
    p.add_argument("--screenshots", default="on-problem", choices=config_mod.SCREENSHOT_MODES)
    a = p.parse_args(argv)
    try:
        checks = check_mod.parse_checks(a.checks)
        result = check_mod.run_check(a.cdp, a.page_url, a.label, checks, a.output, a.screenshots, a.redact_env)
    except check_mod.CheckUsageError as e:
        print(f"qa-audit check: error: {e}", file=sys.stderr)
        return 2
    except check_mod.CheckUnreachable as e:
        print(f"qa-audit check: {e}", file=sys.stderr)
        return 3
    st = result["summary"]["by_status"]
    print(f"{a.label}: {st['problem']} problem(s), {st['warning']} warning(s), {st['needs-human']} needs human"
          f" -- {os.path.join(result['run_folder'], 'check.json')}")
    return check_mod.exit_code(result, a.fail_on)


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
    if argv[:1] == ["check"]:
        return check(argv[1:])
    if argv[:1] == ["doctor"]:
        from .doctor import run as doctor
        return doctor(argv[1:])
    return audit(argv)


if __name__ == "__main__":
    sys.exit(main())
