"""Checks axe-core cannot make: keyboard walk (trap, focus visibility) and reflow at 320 CSS px.

Only the Tab key is pressed: it moves focus and never activates anything.
"""

from __future__ import annotations

import re
import time

DATA_URI = re.compile(r"(data:[^\s\"'()<>,;]*[;,])[^\s\"'()<>]{24,}")


def shorten_data_uris(html: str) -> str:
    """A base64 image in an element's HTML fills the evidence with noise: keep its type, drop the payload."""
    return DATA_URI.sub(lambda m: m.group(1) + "…", html)

CSS_PATH_JS = """
window.__qaCssPath = window.__qaCssPath || function (el) {
  if (!(el instanceof Element)) return '';
  const parts = [];
  while (el && el.nodeType === 1 && parts.length < 5) {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) {
      parts.unshift('#' + CSS.escape(el.id)); break; }
    let part = el.tagName.toLowerCase();
    const parent = el.parentElement;
    if (parent) {
      const same = [...parent.children].filter(c => c.tagName === el.tagName);
      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
    }
    parts.unshift(part);
    el = parent;
  }
  return parts.join(' > ');
};
"""

FOCUS_STATE_JS = """() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const snap = () => { const s = getComputedStyle(el);
    return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.borderColor, s.borderWidth,
            s.backgroundColor, s.color, s.textDecorationLine].join('|'); };
  const focused = snap();
  el.blur();
  const blurred = snap();
  el.focus({preventScroll: true});
  return {selector: window.__qaCssPath(el), html: el.outerHTML.slice(0, 2000),
          changes: focused !== blurred, refocused: document.activeElement === el};
}"""

# the focused element's box plus a margin, clamped to the viewport (screenshot clip coordinates), or why not
FOCUS_BOX_JS = """(margin) => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return {reason: 'no focused element'};
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return {reason: 'the element has no size'};
  const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  const x = Math.max(0, Math.floor(r.left - margin)), y = Math.max(0, Math.floor(r.top - margin));
  const right = Math.min(vw, Math.ceil(r.right + margin)), bottom = Math.min(vh, Math.ceil(r.bottom + margin));
  if (right - x < 1 || bottom - y < 1) return {reason: 'the element is outside the viewport'};
  return {clip: {x: x, y: y, width: right - x, height: bottom - y}};
}"""

SAVE_VIEW_JS = """() => { window.__qaBefore = {el: document.activeElement, x: window.scrollX, y: window.scrollY};
  if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); }"""
RESTORE_VIEW_JS = """() => { const b = window.__qaBefore; if (!b) return;
  if (b.el && b.el !== document.body && b.el.isConnected) b.el.focus({preventScroll: true});
  else if (document.activeElement) document.activeElement.blur();
  window.scrollTo(b.x, b.y); delete window.__qaBefore; }"""
BLUR_JS = "() => { window.__qaFocused = document.activeElement; window.__qaFocused.blur(); }"
REFOCUS_JS = "() => { window.__qaFocused.focus({preventScroll: true}); delete window.__qaFocused; }"

# is there a tabbable element after the focused one? Firefox headless keeps the focus on the last element when
# Tab is pressed at the end of the page (there is no browser UI to move to): that is the end, not a trap
MORE_AFTER_JS = """() => {
  const el = document.activeElement;
  const sel = 'a[href], area[href], button, input:not([type=hidden]), select, textarea, summary, iframe, ' +
              '[tabindex], [contenteditable=""], [contenteditable=true]';
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  return [...document.querySelectorAll(sel)].some(e => e !== el && !el.contains(e) && !e.disabled &&
    e.tabIndex >= 0 && vis(e) && (el.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING));
}"""

REFLOW_JS = """() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"""


def _shot(page, clip) -> bytes | None:
    # finite transitions jump to their end (a focus ring that fades in is compared at full strength); the caret is
    # hidden by default. Identical rendering gives identical PNG bytes, so bytes are compared directly.
    try:
        return page.screenshot(clip=clip, animations="disabled", timeout=5000)
    except Exception:  # noqa: BLE001 - no pixels: the stop stays undecided
        return None


def focus_pixels(page, before: bytes | None, clip: dict | None, settle_ms: int = 100) -> dict:
    """Compare the focused element's area (screenshot `before`, taken right after Tab) with the same area blurred.
    {"same": bool} or {"undecided": reason}; "focused"/"blurred" hold the PNGs when same."""
    if clip is None or before is None:
        return {"undecided": "no screenshot of the element"}
    page.wait_for_timeout(settle_ms)
    again = _shot(page, clip)
    if again != before:            # still changing on its own (script animation, video, GIF), or not captured
        return {"undecided": "the area keeps changing on its own"}
    page.evaluate(BLUR_JS)
    try:
        blurred = _shot(page, clip)
    finally:
        page.evaluate(REFOCUS_JS)
    if blurred is None:
        return {"undecided": "no screenshot of the element"}
    if blurred == before:
        return {"same": True, "focused": before, "blurred": blurred}
    return {"same": False}


def keyboard_walk(page, max_stops: int, margin: int = 8) -> dict:
    """Tab through the page. Returns {"stops": [...], "trap": stop|None, "not_visible": [...], "undecided": [...],
    "pixel_checks": n, "seconds": s}. A stop whose computed style does not change when focused is decided by pixels:
    identical screenshots of its box plus `margin` px, focused and blurred, mean no visible focus. Focus and
    scroll are put back where they were at the end."""
    started = time.monotonic()
    page.evaluate(CSS_PATH_JS)
    page.evaluate(SAVE_VIEW_JS)
    stops, not_visible, undecided, trap = [], [], [], None
    first, repeat, pixel_checks = None, 0, 0
    try:
        for _ in range(max_stops):
            page.keyboard.press("Tab")
            box = page.evaluate(FOCUS_BOX_JS, margin)
            clip = box.get("clip")
            shot = _shot(page, clip) if clip else None      # before FOCUS_STATE_JS blurs and refocuses
            state = page.evaluate(FOCUS_STATE_JS)
            if state is None:
                break                      # focus left the document: the walk is complete
            state["html"] = shorten_data_uris(state["html"])[:300]
            if stops and state["selector"] == stops[-1]["selector"]:
                repeat += 1
                if repeat >= 2:            # three Tabs on the same element
                    if page.evaluate(MORE_AFTER_JS):
                        trap = state       # stuck, although more tabbable elements follow
                    break                  # otherwise: the end of the page
                continue
            repeat = 0
            if first is None:
                first = state["selector"]
            elif state["selector"] == first:
                break                      # cycled back to the first stop
            stops.append(state)
            if state["changes"]:
                continue
            if not clip:
                undecided.append({**state, "reason": box.get("reason", "no screenshot of the element")})
                continue
            pixel_checks += 1
            px = focus_pixels(page, shot, clip)
            if "undecided" in px:
                undecided.append({**state, "reason": px["undecided"]})
            elif px["same"]:
                not_visible.append({**state, "focused_png": px["focused"], "blurred_png": px["blurred"]})
    finally:
        page.evaluate(RESTORE_VIEW_JS)
    return {"stops": stops, "trap": trap, "not_visible": not_visible, "undecided": undecided,
            "pixel_checks": pixel_checks, "seconds": round(time.monotonic() - started, 2)}


def reflow(page, width: int, capture=None) -> dict | None:
    """Horizontal overflow at the given width, or None. capture() runs at that width (evidence screenshot)."""
    original = page.viewport_size
    if not original:
        return None
    try:
        page.set_viewport_size({"width": width, "height": original["height"]})
        page.wait_for_timeout(300)
        m = page.evaluate(REFLOW_JS)
        if m["scroll"] > m["client"] + 2:
            return {"scroll_width": m["scroll"], "client_width": m["client"], "width": width,
                    "shot": capture() if capture else None}
        return None
    finally:
        page.set_viewport_size(original)
