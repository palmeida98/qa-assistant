"""
python -m qa_ui [<workspace>] [--port 8765] [--no-open] [--claude PATH] [--max-turns 150] [--timeout-minutes 60]

Without a workspace: the hub, every project of <qa-assistant>/../qa-workspaces (docs/qa-ui.md section 14). With one:
the interface of that workspace (sections 1 to 13). On the loopback address, opened in the browser.
Exit codes: 0 stopped by the user (Ctrl+C) · 2 usage or configuration (no project.yaml, the port is taken, the
hub's root cannot be created).
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser

from . import hub, runs, server, workspace


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qa_ui", description="Local web interface: the hub of every project, "
                                                                    "or one workspace (docs/qa-ui.md).")
    ap.add_argument("workspace", nargs="?", help="one workspace; without it, the hub over ../qa-workspaces")
    ap.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (0: a free one; default 8765)")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser: only print the address")
    ap.add_argument("--claude", metavar="PATH", help="the Claude Code executable (default: `claude` on the PATH)")
    ap.add_argument("--max-turns", type=int, default=150, help="turn limit of a batch run (default 150)")
    ap.add_argument("--timeout-minutes", type=float, default=60, help="time limit of a run (default 60)")
    a = ap.parse_args(argv)
    if a.max_turns < 1 or a.timeout_minutes <= 0 or not 0 <= a.port <= 65535:
        print("ERROR: --max-turns and --timeout-minutes must be positive, --port between 0 and 65535",
              file=sys.stderr)
        return 2
    if a.workspace is None:
        return main_hub(a)
    ws = os.path.abspath(a.workspace)
    try:
        proj = workspace.project(ws)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    runner = runs.Runner(ws, claude=a.claude, max_turns=a.max_turns, timeout_minutes=a.timeout_minutes)
    try:
        srv, url = server.serve(ws, runner, a.port)
    except OSError as e:
        print(f"ERROR: port {a.port} on 127.0.0.1 cannot be used ({e}): try --port 0", file=sys.stderr)
        return 2
    print(f"qa-ui — {proj.get('project') or os.path.basename(ws)} ({ws})")
    print(f"Open: {url}")
    print("The address holds this start's token: do not share it. Ctrl+C stops the interface (and a run in "
          "progress).")
    if not runner.claude():
        print("WARNING: Claude Code was not found (`claude` on the PATH, or --claude PATH): batches cannot start; "
              "the automated audits can.", file=sys.stderr)
    if not a.no_open:
        webbrowser.open(url)
    return serve_until_stopped(srv, [runner])


def main_hub(a, root: str | None = None) -> int:
    """The hub over the root (docs/qa-ui.md section 14.1)."""
    root = os.path.abspath(root or hub.default_root())
    if not os.path.isdir(root):
        print(f"The hub's root {root} does not exist: it is created empty (docs/qa-ui.md section 14.1).")
        try:
            os.makedirs(root)
        except OSError as e:
            print(f"ERROR: the root {root} cannot be created ({e})", file=sys.stderr)
            return 2
    the_hub = hub.Hub(root, claude=a.claude, max_turns=a.max_turns, timeout_minutes=a.timeout_minutes)
    try:
        srv, url = server.serve_hub(the_hub, a.port)
    except OSError as e:
        print(f"ERROR: port {a.port} on 127.0.0.1 cannot be used ({e}): try --port 0", file=sys.stderr)
        return 2
    n = len(the_hub.projects())
    print(f"qa-ui hub — {n} project(s) in {root}")
    print(f"Open: {url}")
    print("The address holds this start's token: do not share it. Ctrl+C stops the interface (and a run in "
          "progress).")
    if not a.no_open:
        webbrowser.open(url)
    return serve_until_stopped(srv, the_hub.gate.runners)


def serve_until_stopped(srv, runners) -> int:
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        for runner in list(runners):
            runner.shutdown()
        srv.server_close()
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
