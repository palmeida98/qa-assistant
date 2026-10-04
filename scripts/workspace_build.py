"""
The app's builds as releases of the workspace's private repository (technique-mobile.md §11; plan 11.1). The build
never enters git (methodology 4, step 4): a release holds it as an asset, and project.yaml records its tag and SHA-256.

    python scripts/workspace_build.py <workspace> publish <app.apk> [--yes] [--allow-prod] [--i-confirm-private]
    python scripts/workspace_build.py <workspace> fetch [--tag T] [--dest app]

- publish: reads the package, versionName and versionCode from the APK's binary manifest (scripts/apk_manifest.py,
  no aapt2) and its SHA-256. Without --yes it only prints what it would do (a dry run, exit 0). With --yes (after the
  user said yes): `gh release create build-<versionName>-<versionCode>` with the APK as asset and the SHA-256 in the
  notes, then mobile.build in project.yaml becomes {source: "release <tag>", sha256, version: "<name> (<code>)"}
  (only that line is rewritten: the file's comments stay) and project.yaml is committed (never pushed). Refused: a
  package other than mobile.app_id, a production build (the rule of setup_emulator.py; --allow-prod only with the
  user's explicit approval), a public repository (or one whose visibility gh cannot confirm, unless
  --i-confirm-private), and a tag that already exists with another SHA-256 (the same one: nothing to publish, only
  project.yaml is brought up to date).
- fetch: `gh release download <tag>` of the APK into --dest (default app/, ignored by git), renamed <tag>.apk, and
  its SHA-256 checked: against mobile.build.sha256 when the tag is mobile.build's (the default), else against the
  one in the release's notes. On a runner, writes `apk=<path>` to $GITHUB_OUTPUT.
- The repository is the workspace's origin (or $GITHUB_REPOSITORY on a runner). gh gets argument lists; one
  function (the tests replace it).

Exit codes: 0 ok (or dry run) · 1 refused by a safety rule (package, production build, public repository, SHA-256
that does not match) · 2 usage or configuration (no mobile.app_id, not an APK, no release tag known) · 3 gh or git
failed (not found, network)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import workspace_git as wg
from apk_manifest import ManifestError, read_manifest
from common import read_project
from setup_emulator import PROD, sha256_of

SOURCE = re.compile(r"^release\s+(\S+)$")
NOTES_SHA = re.compile(r"sha256:\s*([0-9a-fA-F]{64})")
TAG = re.compile(r"[A-Za-z0-9._-]+")
DEFAULT_DEST = "app"


class Refused(Exception):
    """A safety rule (code 1)."""


class UsageError(Exception):
    """Usage or configuration (code 2)."""


class Failed(Exception):
    """gh or git failed (code 3)."""


# --- the door to gh (the tests replace it) ----------------------------------------------------------------------

def gh(*args: str) -> subprocess.CompletedProcess | None:
    """Runs gh; None when it is not installed."""
    exe = shutil.which("gh")
    if not exe:
        return None
    return subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=False, timeout=900)


def gh_ok(*args: str) -> str:
    r = gh(*args)
    if r is None:
        raise Failed("gh is not installed (https://cli.github.com), or not on the PATH")
    if r.returncode != 0:
        raise Failed(f"gh {args[0]} {args[1] if len(args) > 1 else ''}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


# --- configuration ----------------------------------------------------------------------------------------------

def mobile_of(workspace: str) -> dict:
    try:
        m = read_project(workspace).get("mobile") or {}
    except FileNotFoundError as e:
        raise UsageError(str(e)) from None
    if not isinstance(m, dict) or not m.get("app_id"):
        raise UsageError("mobile.app_id missing in project.yaml (the app under test, e.g. com.demoshop.app)")
    return m


def repository(workspace: str) -> str:
    """OWNER/NAME of the workspace's repository: $GITHUB_REPOSITORY on a runner, else the origin."""
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    slug = wg.repo_slug(wg.remote_url(workspace)) if wg.inside_repository(workspace) else None
    if not slug:
        raise UsageError("the workspace has no GitHub origin: create its private remote first "
                         "(workspace_git.py <workspace> remote --create OWNER/NAME)")
    return slug


def build_tag(mobile: dict) -> str | None:
    """The tag of mobile.build.source ("release <tag>"), or None."""
    build = mobile.get("build") if isinstance(mobile.get("build"), dict) else {}
    m = SOURCE.match(str(build.get("source") or "").strip())
    return m.group(1) if m else None


def tag_for(manifest: dict) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", manifest["version_name"] or "").strip("-") or "unnamed"
    return f"build-{name}-{manifest['version_code']}"


def release_sha(slug: str, tag: str) -> str | None | bool:
    """The SHA-256 in an existing release's notes; None when the release does not exist; False when it exists
    without one."""
    r = gh("release", "view", tag, "--repo", slug, "--json", "body")
    if r is None:
        raise Failed("gh is not installed (https://cli.github.com), or not on the PATH")
    if r.returncode != 0:
        if "not found" in (r.stderr + r.stdout).lower():
            return None
        raise Failed(f"gh release view {tag}: {(r.stderr or r.stdout).strip()[:300]}")
    m = NOTES_SHA.search(r.stdout)
    return m.group(1).lower() if m else False


# --- project.yaml: one line rewritten, the comments kept -----------------------------------------------------------

def set_build(workspace: str, source: str, sha256: str, version: str) -> None:
    """mobile.build becomes a one-line map; the rest of project.yaml is untouched. Checked by reading it back."""
    path = os.path.join(workspace, "project.yaml")
    with open(path, encoding="utf-8-sig") as f:
        original = f.read()
    lines = original.split("\n")
    start = next((i for i, ln in enumerate(lines) if re.match(r"^mobile:\s*(#.*)?$", ln)), None)
    if start is None:
        raise UsageError("project.yaml has no block `mobile:` (a map, one key per line) to record the build in")
    end = next((i for i in range(start + 1, len(lines)) if lines[i] and not lines[i][0].isspace()
                and not lines[i].startswith("#")), len(lines))
    child = next((ln for ln in lines[start + 1:end] if ln.strip() and not ln.lstrip().startswith("#")), None)
    indent = (child[:len(child) - len(child.lstrip())] if child else "  ")
    value = f'{indent}build: {{source: "{source}", sha256: "{sha256}", version: "{version}"}}'
    at = next((i for i in range(start + 1, end) if re.match(rf"^{indent}build:", lines[i])), None)
    if at is None:
        lines.insert(start + 1, value)
    else:
        stop = at + 1
        while stop < end and (not lines[stop].strip() or len(lines[stop]) - len(lines[stop].lstrip()) > len(indent)):
            stop += 1
        while stop > at + 1 and not lines[stop - 1].strip():
            stop -= 1
        lines[at:stop] = [value]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    build = (read_project(workspace).get("mobile") or {}).get("build") or {}
    if (build.get("source"), build.get("sha256"), build.get("version")) != (source, sha256, version):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(original)
        raise UsageError("mobile.build could not be written safely (unusual layout of project.yaml): restored; set "
                         f'it by hand: build: {{source: "{source}", sha256: "{sha256}", version: "{version}"}}')


# --- publish ----------------------------------------------------------------------------------------------------

def check_private(workspace: str, slug: str, confirmed: bool) -> None:
    vis, why = wg.visibility(f"https://github.com/{slug}")
    if vis in ("PUBLIC", "INTERNAL"):
        raise Refused(f"the repository {slug} is {vis}: a build is the client's artefact, it goes only to a private "
                      "repository")
    if vis != "PRIVATE" and not confirmed:
        raise Refused(f"the visibility of {slug} could not be confirmed ({why}); check it and, if it is private, run "
                      "again with --i-confirm-private")


def cmd_publish(workspace: str, a) -> int:
    mobile = mobile_of(workspace)
    if not os.path.isfile(a.apk):
        raise UsageError(f"{a.apk}: the file does not exist")
    try:
        manifest = read_manifest(a.apk)
    except ManifestError as e:
        raise UsageError(str(e)) from None
    if manifest["version_code"] is None:
        raise UsageError("the APK's manifest has no versionCode")
    if manifest["package"] != str(mobile["app_id"]):
        raise Refused(f"the APK is {manifest['package']}, mobile.app_id is {mobile['app_id']}: not the app under test")
    hits = [x for x in (os.path.basename(a.apk), manifest["version_name"] or "") if PROD.search(x)]
    if hits and not a.allow_prod:
        raise Refused(f"production build? {hits[0]!r} contains 'prod' or 'release-store': stop and ask "
                      "(technique-mobile.md §3); --allow-prod only with the user's explicit approval")
    digest = sha256_of(a.apk)
    tag = tag_for(manifest)
    version = f"{manifest['version_name']} ({manifest['version_code']})"
    slug = repository(workspace)
    print(f"apk: {manifest['package']} {version}, minSdk {manifest['min_sdk']}, targetSdk {manifest['target_sdk']}")
    print(f"sha256: {digest}")
    print(f"release: {tag} in {slug}")
    check_private(workspace, slug, a.i_confirm_private)
    existing = release_sha(slug, tag)
    if existing is False:
        raise Refused(f"the release {tag} exists without a sha256 in its notes: it cannot be checked; publish under "
                      "another tag or fix its notes by hand")
    if existing and existing != digest:
        raise Refused(f"the release {tag} exists with another SHA-256 ({existing}): the same version with a different "
                      "build; ask for a new versionCode")
    notes = (f"Build of {manifest['package']} {version} for the tests (the client's artefact: private).\n\n"
             f"sha256: {digest}\n")
    create = ["release", "create", tag, os.path.abspath(a.apk), "--repo", slug, "--title", f"Build {version}",
              "--notes", notes]
    if not a.yes:
        print("would run: gh " + " ".join(c if c != notes else "<notes with the sha256>" for c in create[:-1])
              + (" (the release exists with the same SHA-256: not created again)" if existing else ""))
        print(f'would set mobile.build: {{source: "release {tag}", sha256: "{digest}", version: "{version}"}} and '
              "commit project.yaml")
        print("dry run: nothing done -- ask the user; with their yes, run again with --yes")
        return 0
    if existing:
        print(f"release {tag}: already published with this SHA-256")
    else:
        gh_ok(*create)
        print(f"release {tag}: created with {os.path.basename(a.apk)}")
    set_build(workspace, f"release {tag}", digest, version)
    print(f"project.yaml: mobile.build = release {tag}")
    if wg.own_repository(workspace):
        if wg.git(workspace, "diff", "--quiet", "--", "project.yaml").returncode != 0:
            wg.git_ok(workspace, "add", "--", "project.yaml")
            wg.git_ok(workspace, "commit", "-q", "-m", f"build: {tag}", "--", "project.yaml")
            print(f"commit: build: {tag} (not pushed: push only with the user's approval)")
        else:
            print("commit: project.yaml already had this build")
    return 0


# --- fetch ------------------------------------------------------------------------------------------------------

def cmd_fetch(workspace: str, a) -> int:
    mobile = mobile_of(workspace)
    default = build_tag(mobile)
    tag = a.tag or default
    if not tag:
        raise UsageError("no tag: mobile.build.source is not \"release <tag>\" (publish the build first) and no --tag")
    if not TAG.fullmatch(tag):
        raise UsageError(f"{tag!r} is not a release tag")
    slug = repository(workspace)
    if tag == default:
        expected = str((mobile.get("build") or {}).get("sha256") or "").lower()
        where = "mobile.build.sha256"
    else:
        found = release_sha(slug, tag)
        if found is None:
            raise UsageError(f"the release {tag} does not exist in {slug}")
        expected, where = (found or ""), "the release's notes"
    if not expected:
        raise Refused(f"no SHA-256 to check the build against ({where} is empty): record it first")
    dest = a.dest if os.path.isabs(a.dest) else os.path.join(workspace, a.dest)
    os.makedirs(dest, exist_ok=True)
    target = os.path.join(dest, f"{tag}.apk")
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        gh_ok("release", "download", tag, "--repo", slug, "--pattern", "*.apk", "--dir", tmp)
        apks = sorted(f for f in os.listdir(tmp) if f.lower().endswith(".apk"))
        if len(apks) != 1:
            raise Refused(f"the release {tag} has {len(apks)} APK asset(s), expected one")
        digest = sha256_of(os.path.join(tmp, apks[0]))
        if digest != expected:
            raise Refused(f"SHA-256 of {apks[0]} is {digest}, {where} is {expected}: not the recorded build, "
                          "nothing kept")
        os.replace(os.path.join(tmp, apks[0]), target)
    print(f"apk: {target} (sha256 matches {where})")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"apk={target}\ntag={tag}\n")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The app's builds as releases of the workspace's private repository.")
    ap.add_argument("workspace")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("publish", help="a release build-<versionName>-<versionCode> with the APK (dry run without "
                                       "--yes)")
    p.add_argument("apk")
    p.add_argument("--yes", action="store_true", help="create it (only after the user said yes)")
    p.add_argument("--allow-prod", action="store_true", help="a build that looks like production (explicit approval)")
    p.add_argument("--i-confirm-private", action="store_true",
                   help="the user checked that the repository is private (when gh cannot confirm it)")
    p = sub.add_parser("fetch", help="download the release's APK and check its SHA-256")
    p.add_argument("--tag", help="release tag (default: the tag of mobile.build.source)")
    p.add_argument("--dest", default=DEFAULT_DEST, help="folder, relative to the workspace (default: app/)")
    a = ap.parse_args(argv)
    workspace = os.path.abspath(a.workspace)
    try:
        if not os.path.isdir(workspace):
            raise UsageError(f"{workspace} is not a folder")
        return {"publish": cmd_publish, "fetch": cmd_fetch}[a.command](workspace, a)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    except UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (Failed, wg.Failed, subprocess.TimeoutExpired) as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    # on Windows, output read through a pipe (e.g. Claude Code) came in cp1252 and accents arrived garbled
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
