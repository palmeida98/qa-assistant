# Destructive-command guard (`scripts/guard_destructive.py`)

> Version 1 · 2026-11-10 · Incident I1 of 09.11.2026: a helper script ran `shutil.rmtree` on a parent folder of the
> temporary area to remove one stray file. Rule: `procedures/rules.md` §12.

## What it does
A Claude Code **PreToolUse** hook. It reads the hook input (JSON on stdin) and, for `Bash`, `Write`, `Edit` and `NotebookEdit`,
refuses (exit 2, reason on stderr) a recursive deletion that is not **strictly inside the scratchpad of the current session**
(`/private/tmp/claude-<uid>/<project>/<session_id>/scratchpad`, also under `/tmp`):
`rm -r/-rf/-R/--recursive`, `find ... -delete` or `-exec rm`, `git clean`, and `rmtree`/`removedirs` named in the command text,
in a python file that the command runs, or in a file being written. Each path must be absolute, without `$`, glob, backquote or
substitution, and resolve (`..`, `~`) to a place under the scratchpad; the scratchpad folder itself is not deletable. Without a
`session_id` nothing is allowed. A non-recursive `rm`, `rmdir` and every other tool are not affected.
`QA_SCRATCHPAD` (folders separated by `:`) adds scratchpads, for tests.

## What it does NOT do (limits)
It reads the command line, python files the command names and the text being written. It cannot see a deletion built at run time
(a path read from a file or a network call), a non-python script, or another interpreter it does not recognise. It is a safety
net for the mistake that happened, not a sandbox, and it is **not active until installed**.

## Platform: POSIX only (macOS, Linux)
The scratchpad it protects is a POSIX path (`/private/tmp/claude-<uid>/…`, also under `/tmp`), `QA_SCRATCHPAD` is split on `:` and
the command is parsed as a POSIX shell line. It is **not supported on Windows**, and its tests are skipped there (reason:
"guard_destructive is a POSIX hook"). Observed on `windows-latest` (11.11.2026, Python 3.12.10): after `os.path.normpath` the paths
use a backslash and never match the scratchpad pattern, so every recursive deletion is refused (it fails closed: safe, and of no
use); the exception is a python script named in the command with a backslash path, which the POSIX parsing mangles
(`C:Users…`), so the script is not read and is not checked. Do not install it on Windows expecting protection.

## Installing it (a configuration change: the user approves it)
In the workspace's `.claude/settings.json` (or the user's `~/.claude/settings.json`), with `<qa>` the QA Assistant checkout:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Write|Edit|NotebookEdit",
        "hooks": [{ "type": "command", "command": "python3 <qa>/scripts/guard_destructive.py" }]
      }
    ]
  }
}
```
Check it with a harmless command such as `rm -rf /private/tmp/claude-0/none` (it must be blocked) before relying on it.

## Tests
`python -m unittest tests.test_guard_destructive` (fictional paths; nothing is ever deleted). Skipped on Windows (POSIX only, above).
