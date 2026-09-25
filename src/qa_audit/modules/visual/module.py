"""Visual module (docs/qa-audit.md §19; README.md next to this file)."""

from __future__ import annotations

import os
import shutil

from ...core.findings import Evidence, Finding
from ...core.module import AuditModule
from . import compare

RULES = {
    "visual-diff": ("The page looks different from its baseline", "needs-human", "medium", "high",
                    "The page matches its baseline, or the change is intended.",
                    "Review the diff images; if the change is intended, run again with --update-baseline."),
    "visual-size-changed": ("The page's rendered size changed", "needs-human", "low", "high",
                            "Same rendered size as the baseline, or an intended change.",
                            "Review the screenshots; update the baseline if intended."),
    "horizontal-overflow": ("The page is wider than the viewport", "warning", "medium", "high",
                            "No horizontal scrolling at this viewport width.",
                            "Find the element that overflows (fixed widths, long words, tables); make it responsive."),
    "viewport-meta-missing": ("No viewport meta tag", "warning", "low", "high",
                              '<meta name="viewport" content="width=device-width, initial-scale=1">',
                              "Add the viewport meta tag so mobile browsers do not render a zoomed-out page."),
}
OVERFLOW_JS = "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"


class Visual(AuditModule):
    name = "visual"
    title = "Visual"
    description = "Screenshots per viewport compared with baselines; horizontal overflow and viewport meta tag."
    id_prefix = "VIS"
    order = 70         # resizes the viewport: after the readers, before accessibility's keyboard walk

    def applicable(self, ctx) -> tuple[bool, str]:
        if not compare.pillow_available():
            return False, "Pillow is not installed (pip install -e .[visual])"
        return True, ""

    def validate_config(self, cfg: dict) -> list[str]:
        problems = []
        vps = cfg.get("viewports") or []
        if not vps:
            problems.append("modules.visual.viewports: at least one viewport")
        for i, v in enumerate(vps):
            if not (isinstance(v, dict) and v.get("name") and isinstance(v.get("width"), int)
                    and isinstance(v.get("height"), int)):
                problems.append(f"modules.visual.viewports[{i}]: needs name, width and height (integers)")
        ratio = cfg.get("max_diff_ratio", 0.001)
        if not isinstance(ratio, (int, float)) or not 0 <= ratio < 1:
            problems.append("modules.visual.max_diff_ratio must be a number from 0 to 1")
        if not isinstance(cfg.get("tolerance", 16), int) or not 0 <= cfg.get("tolerance", 16) <= 255:
            problems.append("modules.visual.tolerance must be an integer from 0 to 255")
        return problems

    # --- per page ------------------------------------------------------------------------------------------
    def on_page(self, page, info, ctx) -> None:
        cfg = ctx.module_config(self.name)
        st = ctx.state(self.name)
        url = info.final_url or info.url
        engine = ctx.session.engine if ctx.session else "browser"
        base_root = compare.root(cfg.get("baseline_dir"))
        if "root" not in st:
            compare.ensure_root(base_root)
            st["root"] = base_root
        if not page.evaluate("() => !!document.querySelector('meta[name=viewport]')"):
            self._hit(st, "viewport-meta-missing", url, "no <meta name=\"viewport\">", "")
        else:
            ctx.passed(self.name)
        original = page.viewport_size
        mask = [page.locator(sel) for sel in cfg.get("mask") or []]
        try:
            for vp in cfg.get("viewports") or []:
                page.set_viewport_size({"width": vp["width"], "height": vp["height"]})
                page.evaluate("() => document.fonts ? document.fonts.ready.then(() => true) : true")
                page.wait_for_timeout(200)
                self._overflow(page, url, vp, ctx, st)
                self._screenshot(page, url, vp, engine, mask, cfg, ctx, st)
        finally:
            if original:
                page.set_viewport_size(original)

    def _overflow(self, page, url, vp, ctx, st) -> None:
        m = page.evaluate(OVERFLOW_JS)
        if m["scroll"] > m["client"] + 2:
            shot = ctx.screenshot(page, f"overflow {vp['name']} {url}")
            self._hit(st, "horizontal-overflow", url,
                      f"{vp['name']} {vp['width']}px: page is {m['scroll']}px wide", shot, viewport=vp["name"])
        else:
            ctx.passed(self.name)

    def _screenshot(self, page, url, vp, engine, mask, cfg, ctx, st) -> None:
        run = ctx.run
        current, current_rel = run.new_file("screenshots", f"visual {vp['name']} {url}", "png")
        page.screenshot(path=current, full_page=True, animations="disabled", caret="hide", mask=mask,
                        timeout=ctx.cfg["browser"]["timeout_ms"])
        compare.crop_height(current, int(cfg.get("max_height", 8000)))
        baseline = compare.baseline_path(st["root"], url, engine, vp["name"])
        label = f"{vp['name']} {vp['width']}x{vp['height']}"
        if cfg.get("update_baseline") or not os.path.isfile(baseline):
            os.makedirs(os.path.dirname(baseline), exist_ok=True)
            shutil.copyfile(current, baseline)
            result = "baseline updated" if cfg.get("update_baseline") else "baseline created"
            st[result] = st.get(result, 0) + 1
            ctx.add_metric(self.name, url, f"visual diff ({label})", None, "%", "", result)
            return
        diff, diff_rel = run.new_file("screenshots", f"diff {vp['name']} {url}", "png")
        r = compare.compare(baseline, current, int(cfg.get("tolerance", 16)), diff)
        threshold = float(cfg.get("max_diff_ratio", 0.001))
        pct = round(r["ratio"] * 100, 3)
        changed = r["ratio"] > threshold
        ctx.add_metric(self.name, url, f"visual diff ({label})", pct, "%", f"<= {threshold * 100:g} %",
                       "changed" if changed else "unchanged")
        if not changed and not r["size_changed"]:
            ctx.passed(self.name)
            if os.path.isfile(diff):
                os.remove(diff)
            return
        base_copy, base_rel = run.new_file("screenshots", f"baseline {vp['name']} {url}", "png")
        shutil.copyfile(baseline, base_copy)
        ev = [Evidence(kind="metric", value=f"{label}: {pct} % of pixels changed; size "
                                            f"{r['baseline_size'][0]}x{r['baseline_size'][1]} -> "
                                            f"{r['current_size'][0]}x{r['current_size'][1]}"),
              Evidence(kind="screenshot", path=base_rel, label=f"baseline {label}"),
              Evidence(kind="screenshot", path=current_rel, label=f"current {label}")]
        if os.path.isfile(diff):
            ev.append(Evidence(kind="screenshot", path=diff_rel, label=f"diff {label}"))
        check = "visual-diff" if changed else "visual-size-changed"
        self._hit(st, check, url, f"{label}: {pct} % changed", None, evidence=ev, viewport=vp["name"])

    # --- findings ------------------------------------------------------------------------------------------
    def _hit(self, st, check, url, text, shot, evidence=None, viewport=""):
        ev = evidence or [Evidence(kind="text", value=text)] + ([shot] if shot else [])
        st.setdefault("hits", {}).setdefault(check, []).append((url, text, ev, viewport))

    def finalize(self, ctx) -> None:
        st = ctx.state(self.name)
        if "root" in st:
            made = ", ".join(f"{st[k]} {k}" for k in ("baseline created", "baseline updated") if st.get(k))
            ctx.notes.append(f"visual: baselines in {st['root']}" + (f" ({made})" if made else "")
                             + "; they are screenshots of the audited site and are not removed by --cleanup "
                               "(qa-audit baselines --delete <host>)")
        for check, hits in st.get("hits", {}).items():
            title, status, severity, confidence, expected, recommendation = RULES[check]
            url0 = hits[0][0]
            pages = list(dict.fromkeys(u for u, *_ in hits))
            ev = [e for _, _, evs, _ in hits[:3] for e in evs]
            if check == "horizontal-overflow" and all(v == "desktop" for *_, v in hits):
                severity = "low"
            ctx.add_finding(Finding(
                module=self.name, check=check, title=title, status=status, severity=severity,
                confidence=confidence, url=url0, expected=expected, recommendation=recommendation,
                actual="; ".join(f"{u}: {t}" for u, t, *_ in hits[:10]) + (f" (+{len(hits) - 10} more)"
                                                                           if len(hits) > 10 else ""),
                description="Pages: " + ", ".join(pages[:10]),
                steps=[f"Open {url0}", "Set the browser to the viewport named in the finding",
                       "Compare with the evidence"],
                evidence=ev))


MODULE = Visual()
