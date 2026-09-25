"""Functional module (docs/qa-audit.md §18; README.md next to this file)."""

from __future__ import annotations

import os
import re
import time

from ...browser.session import origin
from ...core.findings import Evidence, Finding
from ...core.module import AuditModule
from . import checks

MDN_STATUS = "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status"
# check -> (title, status, severity, confidence, expected, recommendation)
RULES = {
    "broken-link": ("Links to pages that do not exist (4xx)", "problem", "medium", "high",
                    "Every link leads to a page that answers 2xx/3xx.",
                    "Fix or remove the links, or restore the pages (redirect moved pages)."),
    "broken-link-server-error": ("Links to pages that fail on the server (5xx)", "problem", "high", "high",
                                 "Linked pages answer without server errors.",
                                 "Investigate the server errors (logs) for these URLs."),
    "broken-external-link": ("External links that fail", "warning", "low", "medium",
                             "External links lead to pages that answer 2xx/3xx.",
                             "Update or remove the external links."),
    "broken-resource": ("Resources of the site fail to load", "problem", "medium", "high",
                        "Every image, script, stylesheet and request of the page loads.",
                        "Fix the paths or restore the files; check the server logs for 5xx."),
    "broken-resource-third-party": ("Third-party resources fail to load", "warning", "low", "high",
                                    "Third-party resources load.",
                                    "Check the third-party URLs, or remove resources that are gone."),
    "js-exception": ("Uncaught JavaScript exceptions", "warning", "medium", "high",
                     "Pages load without uncaught exceptions.",
                     "Fix the exceptions (see the messages; reproduce with the browser console open)."),
    "console-error": ("Errors written to the browser console", "warning", "low", "high",
                      "No console errors while the page loads.", "Review and fix the logged errors."),
    "redirect-chain": ("Links reach their page through long redirect chains", "warning", "low", "high",
                       "At most two redirects to reach a page.",
                       "Point the links (and first redirects) directly at the final URL."),
    "form-email-type": ("E-mail fields accept any text", "warning", "low", "medium",
                        "E-mail fields use type=\"email\" (or a pattern) so the browser validates them.",
                        "Use <input type=\"email\"> (plus server-side validation)."),
    "form-submit-failed": ("A listed form fails when submitted", "problem", "medium", "high",
                           "The form submits and the response is 2xx/3xx, without exceptions.",
                           "Reproduce with the same data in the test environment and check the server logs."),
    "form-submit-no-navigation": ("A listed form did not navigate after submit", "needs-human", "info", "medium",
                                  "A person confirms what the form does when submitted.",
                                  "Check the form by hand: it may be handled by a script without navigation."),
}
NEVER_REQUEST = re.compile(r"^(excluded|disallowed by robots|scheme )")


class Functional(AuditModule):
    name = "functional"
    title = "Functional"
    description = "Broken links and resources, JavaScript errors, redirect chains, form checks; opt-in form submit."
    id_prefix = "FUN"
    order = 20       # reads the page; listed forms are submitted in their own tab after the crawl

    def validate_config(self, cfg: dict) -> list[str]:
        problems = []
        for key in ("max_link_checks",):
            if not isinstance(cfg.get(key, 0), int) or cfg.get(key, 0) < 0:
                problems.append(f"modules.functional.{key} must be an integer >= 0")
        for i, form in enumerate(cfg.get("forms") or []):
            where = f"modules.functional.forms[{i}]"
            if not isinstance(form, dict) or not form.get("page") or not form.get("selector"):
                problems.append(f"{where}: needs page (URL regular expression) and selector")
                continue
            try:
                re.compile(form["page"])
            except re.error as e:
                problems.append(f"{where}.page: invalid regular expression: {e}")
            for key in ("fields", "fields_env"):
                if not isinstance(form.get(key, {}), dict):
                    problems.append(f"{where}.{key} must be a mapping")
        return problems

    # --- per page ------------------------------------------------------------------------------------------
    def on_page(self, page, info, ctx) -> None:
        st = ctx.state(self.name)
        url = info.final_url or info.url
        own, third = checks.resource_failures(info.failed_requests, origin(url))
        for check, items in (("broken-resource", own), ("broken-resource-third-party", third)):
            if items:
                self._hit(st, check, url, [checks.describe_failure(r) for r in items])
            else:
                ctx.passed(self.name)
        for check, items in (("js-exception", info.page_errors), ("console-error",
                                                                   checks.console_errors(info.console_errors))):
            if items:
                self._hit(st, check, url, list(dict.fromkeys(items)))
            else:
                ctx.passed(self.name)
        emails = checks.email_fields_without_validation(info.forms)
        if emails:
            self._hit(st, "form-email-type", url, [f"<input name=\"{f['name']}\" type=\"{f['type'] or 'text'}\"> "
                                                   f"(label: {f['label'] or '-'})" for f in emails])
        elif info.forms:
            ctx.passed(self.name)

    # --- after the crawl -----------------------------------------------------------------------------------
    def finalize(self, ctx) -> None:
        cfg = ctx.module_config(self.name)
        st = ctx.state(self.name)
        crawl = ctx.crawl
        if crawl is None or ctx.session is None:
            return
        self._links(crawl, cfg, ctx, st)
        self._forms(cfg, ctx)
        self._report(st, int(cfg.get("max_items_listed", 10)), ctx)

    def _links(self, crawl, cfg, ctx, st) -> None:
        referrers: dict[str, list[str]] = {}
        external: dict[str, list[str]] = {}
        for p in crawl.pages:
            if p.audited:
                src = p.final_url or p.url
                for link in p.links:
                    referrers.setdefault(link, []).append(src)
                for link in p.external_links:
                    external.setdefault(link, []).append(src)
        visited = {p.url: p for p in crawl.pages}
        never = {s["url"] for s in crawl.skipped if NEVER_REQUEST.match(s["reason"])}
        broken: dict[str, list] = {}

        def judge(url: str, status, error: str = ""):
            check = checks.link_status_check(status) or ("broken-link" if error else None)
            if check:
                what = f"HTTP {status}" if status else error
                broken.setdefault(check, []).append(f"{url}: {what}; linked from {', '.join(referrers[url][:3])}")
            else:
                ctx.passed(self.name)

        for url in referrers:
            if url in visited:
                p = visited[url]
                judge(url, p.status, "" if p.status else p.error)
                if len(p.redirects) >= 3:
                    self._hit(st, "redirect-chain", url, [" -> ".join([*p.redirects, p.final_url])])
        # links the crawl did not visit (depth, max pages, non-HTML): HEAD, or GET when HEAD is refused
        pending = [u for u in referrers if u not in visited and u not in never]
        limit = int(cfg.get("max_link_checks", 50))
        delay = ctx.cfg["crawl"]["delay_ms"] / 1000
        for i, url in enumerate(pending[:limit]):
            if i and delay:
                time.sleep(delay)
            status, error = self._probe(url, ctx)
            judge(url, status, error)
        if len(pending) > limit:
            ctx.notes.append(f"functional: {len(pending) - limit} same-origin link(s) not checked "
                             f"(max_link_checks {limit})")
        if cfg.get("check_external_links"):
            for i, url in enumerate(list(external)[:limit]):
                if i and delay:
                    time.sleep(delay)
                status, error = self._probe(url, ctx)
                if (status and status >= 400) or error:
                    broken.setdefault("broken-external-link", []).append(
                        f"{url}: {f'HTTP {status}' if status else error}; linked from {', '.join(external[url][:3])}")
        for check, items in broken.items():
            first_ref = items[0].rsplit("linked from ", 1)[-1].split(",")[0]
            self._hit(st, check, first_ref, items)

    def _probe(self, url: str, ctx) -> tuple[int | None, str]:
        req, timeout = ctx.session.request(), ctx.cfg["browser"]["timeout_ms"]
        try:
            resp = req.head(url, max_redirects=5, timeout=timeout)
            if resp.status in (405, 501):
                resp = req.get(url, max_redirects=5, timeout=timeout)
            return resp.status, ""
        except Exception as e:  # noqa: BLE001 - a network error on a same-origin link is a finding
            return None, (str(e).splitlines()[0] if str(e) else type(e).__name__)[:200]

    def _forms(self, cfg, ctx) -> None:
        forms = cfg.get("forms") or []
        if not forms:
            return
        if not ctx.cfg["safety"].get("allow_submit"):
            ctx.notes.append(f"functional: {len(forms)} listed form(s) not submitted (--allow-submit not given)")
            return
        audited = [p.final_url or p.url for p in ctx.pages if p.audited]
        submitted = 0
        for spec in forms:
            url = next((u for u in audited if re.search(spec["page"], u)), None)
            if url is None:
                ctx.notes.append(f"functional: no audited page matches form page {spec['page']!r}; not submitted")
                continue
            values = {k: str(v) for k, v in (spec.get("fields") or {}).items()}
            missing = [v for v in (spec.get("fields_env") or {}).values() if not os.environ.get(v)]
            if missing:
                ctx.notes.append(f"functional: form on {url} not submitted: environment variable(s) "
                                 f"{', '.join(missing)} not set")
                continue
            values.update({k: os.environ[v] for k, v in (spec.get("fields_env") or {}).items()})
            if self._submit(url, spec["selector"], values, ctx):
                submitted += 1
        ctx.notes.append(f"functional: {submitted} listed form(s) submitted (--allow-submit)")

    def _submit(self, url: str, selector: str, values: dict, ctx) -> bool:
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import TimeoutError as PWTimeout

        page = ctx.session.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        try:
            page.goto(url, wait_until="load")
            form = page.locator(selector).first
            if form.count() == 0:
                ctx.notes.append(f"functional: form {selector!r} not found on {url}; not submitted")
                return False
            for name, value in values.items():
                field = form.locator(f'[name="{name}"]').first
                if field.evaluate("e => e.tagName") == "SELECT":
                    field.select_option(value)
                else:
                    field.fill(value)
            steps = [f"Open {url}", f"Fill the form {selector} with the configured test data", "Submit it"]
            try:
                with page.expect_navigation(timeout=ctx.cfg["browser"]["timeout_ms"]) as nav:
                    form.evaluate("f => f.requestSubmit ? f.requestSubmit() : f.submit()")
                resp = nav.value
            except PWTimeout:
                ctx.add_finding(self._finding("form-submit-no-navigation", url, steps, [], actual="no navigation"))
                return True
            status = resp.status if resp else None
            observed = f"{resp.request.method if resp else 'submit'} {page.url} -> HTTP {status}"
            if (status and status >= 400) or errors:
                ev = [Evidence(kind="text", value=observed)] + [Evidence(kind="log", value=e) for e in errors[:3]]
                shot = ctx.screenshot(page, f"form submit {url}", full_page=True)
                if shot:
                    ev.append(shot)
                f = self._finding("form-submit-failed", url, steps, ev, actual=observed)
                if status and status >= 500:
                    f.severity = "high"
                ctx.add_finding(f)
            else:
                ctx.passed(self.name)
            return True
        except PWError as e:
            ctx.errors.append({"module": self.name, "url": url, "message": f"form submit: {str(e).splitlines()[0]}"})
            return False
        finally:
            page.close()

    # --- findings ------------------------------------------------------------------------------------------
    def _hit(self, st, check: str, url: str, items: list[str]) -> None:
        st.setdefault("hits", {}).setdefault(check, []).append((url, items))

    def _report(self, st, max_listed: int, ctx) -> None:
        for check, hits in st.get("hits", {}).items():
            url0 = hits[0][0]
            items = [(u, i) for u, its in hits for i in its]
            listed = [f"{i}" if check.startswith("broken-link") or check == "broken-external-link" else f"{u}: {i}"
                      for u, i in items]
            ev = [Evidence(kind="log" if check in ("js-exception", "console-error") else "text", value=x[:500])
                  for x in listed[:max_listed]]
            more = f" (+{len(listed) - max_listed} more)" if len(listed) > max_listed else ""
            pages = list(dict.fromkeys(u for u, _ in hits))
            ctx.add_finding(self._finding(
                check, url0, [f"Open {url0}", "Open the browser developer tools (Console and Network)",
                              "Reload and compare with the evidence"], ev,
                actual=f"{len(listed)} occurrence(s) on {len(pages)} page(s){more}",
                description="Pages: " + ", ".join(pages[:max_listed])))

    def _finding(self, check, url, steps, evidence, actual="", description="") -> Finding:
        title, status, severity, confidence, expected, recommendation = RULES[check]
        refs = [MDN_STATUS] if check.startswith(("broken", "form-submit")) else []
        return Finding(module=self.name, check=check, title=title, status=status, severity=severity,
                       confidence=confidence, url=url, expected=expected, actual=actual, description=description,
                       recommendation=recommendation, steps=steps, evidence=evidence, references=refs)


MODULE = Functional()
