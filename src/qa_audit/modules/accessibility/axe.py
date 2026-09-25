"""The bundled axe-core (docs/qa-audit.md §11, §14): load, verify and run it. Nothing is fetched at run time."""

from __future__ import annotations

import functools
import hashlib
import json
import os

import yaml

VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")

RUN_JS = """async (opts) => {
  const r = await axe.run(document, {
    runOnly: {type: 'tag', values: opts.tags},
    resultTypes: ['violations', 'incomplete'],
    iframes: false,
  });
  const pick = rule => ({
    id: rule.id, impact: rule.impact, help: rule.help, description: rule.description,
    helpUrl: rule.helpUrl, tags: rule.tags,
    nodes: rule.nodes.slice(0, opts.maxNodes).map(n => ({
      target: n.target, html: n.html, failureSummary: n.failureSummary || '' })),
    total: rule.nodes.length,
  });
  return {version: r.testEngine.version, passes: r.passes.length,
          violations: r.violations.map(pick), incomplete: r.incomplete.map(pick)};
}"""


def manifest() -> dict:
    with open(os.path.join(VENDOR, "axe.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


@functools.lru_cache(maxsize=1)
def source() -> str:
    with open(os.path.join(VENDOR, "axe.min.js"), encoding="utf-8") as f:
        return f.read()


def verify_bundle() -> tuple[bool, str]:
    """(ok, detail): the file matches the manifest (hash and declared version)."""
    try:
        m = manifest()
        with open(os.path.join(VENDOR, "axe.min.js"), "rb") as f:
            data = f.read()
    except OSError as e:
        return False, f"missing: {e}"
    if hashlib.sha256(data).hexdigest() != m.get("sha256"):
        return False, "axe.min.js does not match vendor/axe.yaml (run scripts/update_axe.py)"
    if f"axe v{m.get('version')}".encode() not in data[:500]:
        return False, f"axe.min.js does not declare version {m.get('version')}"
    return True, f"axe-core {m['version']} (sha256 verified)"


class AxeUnavailable(RuntimeError):
    pass


ATTR = "data-qa-audit-axe"

# Set first, in its own statement: if this marker is missing after injection, the page's CSP blocked the script.
# Then: some browser builds throw OperationError from crypto.getRandomValues even in the page's realm (seen in
# Playwright's Firefox on Windows); axe uses it only for internal ids. Only when the native call fails, a Math.random
# substitute is installed while axe loads and runs, and the original is put back. Healthy browsers are untouched.
PRELUDE_JS = f"""
document.documentElement.setAttribute('{ATTR}-started', '1');
var __qaAuditCryptoSubstituted = false;
var __qaAuditRestoreCrypto = function () {{}};
(function () {{
  var C = window.crypto, P = C && Object.getPrototypeOf(C), orig = P && P.getRandomValues;
  if (!orig) return;
  try {{ C.getRandomValues(new Uint8Array(1)); return; }} catch (e) {{ /* broken in this browser build */ }}
  Object.defineProperty(P, 'getRandomValues', {{configurable: true, writable: true, value: function (a) {{
    for (var i = 0; i < a.length; i++) {{ a[i] = Math.floor(Math.random() * 256); }} return a; }}}});
  __qaAuditCryptoSubstituted = true;
  __qaAuditRestoreCrypto = function () {{
    Object.defineProperty(P, 'getRandomValues', {{configurable: true, writable: true, value: orig}}); }};
}})();
"""


def _run_in_page_realm(page, opts: dict, timeout_ms: int) -> dict | None:
    """Load and run axe inside the page's own realm through a <script> element; the result comes back as JSON
    text in an attribute of <html>, removed afterwards. None when the page's CSP blocks inline scripts.

    Why not evaluate: Playwright evaluates from a separate realm; in Firefox that realm sees page objects through
    X-ray wrappers (axe's functions are hidden) and crypto.getRandomValues refuses its typed arrays."""
    from playwright.sync_api import Error as PWError

    script = (PRELUDE_JS + source() + "\n;(function (opts) {"
              "const root = document.documentElement;"
              f"root.setAttribute('{ATTR}-loaded', '1');"
              "const done = r => { __qaAuditRestoreCrypto(); r.cryptoSubstituted = __qaAuditCryptoSubstituted;"
              f" root.setAttribute('{ATTR}', JSON.stringify(r)); }};"
              f"({RUN_JS})(opts).then(done, e => done({{error: String(e)}}));"
              "})(" + json.dumps(opts) + ");")
    try:
        handle = page.add_script_tag(content=script)
        handle.evaluate("e => e.remove()")
    except PWError:
        return None
    try:
        state = page.evaluate(f"() => ['started', 'loaded'].map(k => "
                              f"document.documentElement.hasAttribute('{ATTR}-' + k))")
        if not state[0]:
            return None                        # blocked by the page's Content-Security-Policy
        if not state[1]:
            page.evaluate("() => { try { __qaAuditRestoreCrypto(); } catch (e) {} }")
            raise AxeUnavailable("axe-core failed to load in this browser")
        page.wait_for_function(f"() => document.documentElement.hasAttribute('{ATTR}')", timeout=timeout_ms)
        raw = page.evaluate(f"() => document.documentElement.getAttribute('{ATTR}')")
    finally:
        page.evaluate(f"() => {{ const r = document.documentElement; r.removeAttribute('{ATTR}');"
                      f" r.removeAttribute('{ATTR}-started'); r.removeAttribute('{ATTR}-loaded'); }}")
    data = json.loads(raw)
    if "error" in data:
        raise AxeUnavailable(f"axe-core failed on the page: {data['error']}")
    return data


def _run_by_evaluate(page, opts: dict) -> dict:
    """Fallback for pages whose CSP blocks inline scripts; the page CSP does not apply to evaluate in Chromium."""
    from playwright.sync_api import Error as PWError

    version = manifest()["version"]
    try:
        page.evaluate(source())
        if not page.evaluate("v => typeof window.axe === 'object' && window.axe.version === v", version):
            raise AxeUnavailable("axe-core could not be loaded into the page (Content-Security-Policy)")
        return page.evaluate(RUN_JS, opts)
    except PWError as e:
        raise AxeUnavailable("axe-core could not run on the page (Content-Security-Policy): "
                             f"{str(e).splitlines()[0]}") from e


def run(page, tags: list[str], max_nodes: int, timeout_ms: int = 60000) -> dict:
    """Always our pinned copy, even if the page ships its own axe."""
    opts = {"tags": tags, "maxNodes": max_nodes}
    result = _run_in_page_realm(page, opts, timeout_ms)
    return result if result is not None else _run_by_evaluate(page, opts)
