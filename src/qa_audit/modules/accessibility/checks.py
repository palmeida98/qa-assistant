"""Checks axe-core cannot make: keyboard walk (trap, focus visibility) and reflow at 320 CSS px.

Only the Tab key is pressed: it moves focus and never activates anything.
"""

from __future__ import annotations

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
  return {selector: window.__qaCssPath(el), html: el.outerHTML.slice(0, 300),
          changes: focused !== blurred, refocused: document.activeElement === el};
}"""

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


def keyboard_walk(page, max_stops: int) -> dict:
    """Tab through the page. Returns {"stops": [...], "trap": stop|None, "no_visible_change": [...]}."""
    page.evaluate(CSS_PATH_JS)
    page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); }")
    stops, no_change, trap = [], [], None
    first, repeat = None, 0
    for _ in range(max_stops):
        page.keyboard.press("Tab")
        state = page.evaluate(FOCUS_STATE_JS)
        if state is None:
            break                      # focus left the document: the walk is complete
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
        if not state["changes"]:
            no_change.append(state)
    return {"stops": stops, "trap": trap, "no_visible_change": no_change}


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
