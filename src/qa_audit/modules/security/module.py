"""Passive security module (docs/qa-audit.md §16; README.md next to this file)."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from ...core.findings import Evidence, Finding
from ...core.module import AuditModule
from . import checks

OWASP_HEADERS = "https://owasp.org/www-project-secure-headers/"
MDN = "https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/"

# check -> (title, status, severity, confidence, expected, recommendation, references)
RULES = {
    "not-https": ("The site is served over plain HTTP", "problem", "medium", "high",
                  "Every page is served over HTTPS.",
                  "Serve the site over HTTPS only and redirect http:// to https://.",
                  ["https://owasp.org/www-project-top-ten/2017/A3_2017-Sensitive_Data_Exposure"]),
    "form-to-http": ("A form on an HTTPS page submits to HTTP", "problem", "high", "high",
                     "Forms on HTTPS pages submit to https:// URLs.", "Change the form action to https://.",
                     [OWASP_HEADERS]),
    "mixed-content-active": ("HTTPS page loads scripts, styles or frames over HTTP", "problem", "medium", "high",
                             "An HTTPS page loads every resource over HTTPS.",
                             ("Load these resources over https:// (browsers block active mixed content, so they "
                              "are also broken)."),
                             ["https://developer.mozilla.org/en-US/docs/Web/Security/Mixed_content"]),
    "mixed-content-passive": ("HTTPS page loads images or media over HTTP", "warning", "low", "high",
                              "An HTTPS page loads every resource over HTTPS.", "Load these resources over https://.",
                              ["https://developer.mozilla.org/en-US/docs/Web/Security/Mixed_content"]),
    "http-not-redirected": ("HTTP is not redirected to HTTPS", "warning", "medium", "high",
                            "http:// requests are redirected (301/308) to https://.",
                            "Redirect every http:// request to the same https:// URL, then add HSTS.",
                            [MDN + "Strict-Transport-Security"]),
    "tls-old-protocol": ("Outdated TLS protocol", "problem", "high", "high",
                         "TLS 1.2 or 1.3.", "Disable TLS 1.1 and older on the server.",
                         ["https://datatracker.ietf.org/doc/rfc8996/"]),
    "tls-cert-expiring": ("TLS certificate expires soon", "warning", "medium", "high",
                          "The certificate is renewed well before it expires.",
                          "Renew the certificate (automate renewal).", []),
    "hsts-missing": ("Strict-Transport-Security header missing", "warning", "low", "high",
                     "HTTPS responses send Strict-Transport-Security with a long max-age.",
                     "Add Strict-Transport-Security: max-age=31536000; includeSubDomains.",
                     [OWASP_HEADERS, MDN + "Strict-Transport-Security"]),
    "hsts-short": ("Strict-Transport-Security max-age is short", "warning", "low", "high",
                   "max-age of at least 180 days (one year recommended).", "Raise max-age to 31536000.",
                   [OWASP_HEADERS]),
    "csp-missing": ("Content-Security-Policy missing", "warning", "low", "high",
                    "Pages send a Content-Security-Policy.",
                    "Define a Content-Security-Policy, starting from default-src 'self'.",
                    [OWASP_HEADERS, MDN + "Content-Security-Policy"]),
    "csp-unsafe": ("Content-Security-Policy allows unsafe script sources", "warning", "low", "high",
                   "script-src without 'unsafe-inline', 'unsafe-eval' or *.",
                   "Use nonces or hashes instead of 'unsafe-inline'; avoid 'unsafe-eval' and wildcards.",
                   [MDN + "Content-Security-Policy/script-src"]),
    "clickjacking": ("Page can be framed by other sites (clickjacking)", "warning", "low", "high",
                     "X-Frame-Options DENY/SAMEORIGIN or CSP frame-ancestors.",
                     "Send Content-Security-Policy: frame-ancestors 'self' (or X-Frame-Options: DENY).",
                     [OWASP_HEADERS, MDN + "X-Frame-Options"]),
    "nosniff-missing": ("X-Content-Type-Options: nosniff missing", "warning", "low", "high",
                        "Responses send X-Content-Type-Options: nosniff.", "Add X-Content-Type-Options: nosniff.",
                        [OWASP_HEADERS]),
    "version-disclosure": ("Server software version disclosed in headers", "warning", "low", "high",
                           "Headers do not reveal product versions.",
                           "Remove version numbers from Server / X-Powered-By headers.", [OWASP_HEADERS]),
    "cookie-not-secure": ("Cookie without the Secure flag on an HTTPS site", "problem", "medium", "high",
                          "Cookies of an HTTPS site are Secure.", "Set the Secure attribute on these cookies.",
                          [MDN + "Set-Cookie"]),
    "cookie-samesite-none-insecure": ("Cookie with SameSite=None but not Secure", "problem", "medium", "high",
                                      "SameSite=None cookies are also Secure.", "Add Secure, or use SameSite=Lax.",
                                      [MDN + "Set-Cookie"]),
    "cookie-samesite-unset": ("Cookie without a declared SameSite", "needs-human", "info", "medium",
                              "Each cookie declares SameSite (Lax, Strict, or None with Secure).",
                              ("Declare SameSite on the cookie; without it, cross-site behaviour depends on the "
                               "browser. A cookie set by JavaScript: check the code that sets it."),
                              [MDN + "Set-Cookie"]),
    "cookie-session-not-httponly": ("Session-like cookie readable by JavaScript", "warning", "medium", "medium",
                                    "Session and token cookies are HttpOnly.",
                                    "Set HttpOnly on cookies that carry sessions or tokens.",
                                    [MDN + "Set-Cookie"]),
}
HEADER_CHECKS = ("csp-missing", "csp-unsafe", "clickjacking", "nosniff-missing", "version-disclosure")


class Security(AuditModule):
    name = "security"
    title = "Security"
    description = "Passive checks: HTTPS, TLS, security headers, cookies, mixed content, version disclosure."
    id_prefix = "SEC"
    order = 10         # reads only
    disclaimer = ("Passive checks only (headers, cookies, TLS, page HTML); this is not a penetration test and "
                  "cannot show that a site is secure.")

    # --- per page ------------------------------------------------------------------------------------------
    def on_page(self, page, info, ctx) -> None:
        cfg = ctx.module_config(self.name)
        st = ctx.state(self.name)
        url = info.final_url or info.url
        h, meta = info.headers, info.meta_csp
        https = checks.is_https(url)
        seen = "response headers: " + ", ".join(sorted(h)) if h else "no response headers"
        results = {
            "csp-missing": checks.csp_missing(h, meta),
            "csp-unsafe": checks.csp_unsafe(h, meta),
            "clickjacking": checks.clickjacking(h, meta),
            "nosniff-missing": checks.nosniff_missing(h),
            "version-disclosure": checks.version_disclosure(h),
        }
        if https:
            r = checks.hsts(h, int(cfg.get("hsts_min_age", 15552000)))
            results["hsts"] = r
            active, passive = checks.insecure_references(info.insecure_refs)
            results["mixed-content-active"] = {"observed": f"{len(active)} http:// reference(s)",
                                               "items": active} if active else None
            results["mixed-content-passive"] = {"observed": f"{len(passive)} http:// reference(s)",
                                                "items": passive} if passive else None
            bad_forms = checks.forms_to_http(info.forms)
            results["form-to-http"] = {"observed": "form action " + ", ".join(bad_forms)} if bad_forms else None
            if info.tls and "tls" not in st:
                st["tls"] = (url, info.tls)
        if info.password_fields:
            st.setdefault("password_pages", []).append(url)
        for check, result in results.items():
            if result is None:
                ctx.passed(self.name)
                continue
            if check == "hsts":
                check, result = result
            result = dict(result, seen=seen, password=bool(info.password_fields))
            st.setdefault("hits", {}).setdefault(check, []).append((url, result))

    # --- after the crawl -----------------------------------------------------------------------------------
    def finalize(self, ctx) -> None:
        cfg = ctx.module_config(self.name)
        st = ctx.state(self.name)
        session = ctx.session
        target = session.target if session else ""
        audited = [p for p in ctx.pages if p.audited]
        if not audited or not target:
            return
        https = checks.is_https(target)
        pw_pages = st.get("password_pages", [])
        if not https:
            f = self._finding("not-https", target, [f"GET {target}", "The page loads over http:// without TLS."],
                              [Evidence(kind="text", value=f"audited origin {target} uses http://")])
            if pw_pages:
                f.severity = "high"
                f.description = (f"{len(pw_pages)} page(s) with a password field are sent without encryption: "
                                 + ", ".join(pw_pages[:5]))
            ctx.add_finding(f)
        else:
            ctx.passed(self.name)
            if cfg.get("check_http_redirect", True):
                self._http_redirect(target, ctx)
            if "tls" in st:
                url, tls = st["tls"]
                found = checks.tls_findings(tls, int(cfg.get("cert_expiry_days", 14)))
                for check, data in found:
                    ev = [Evidence(kind="text", value=data["observed"])]
                    if data.get("value"):
                        ev.append(Evidence(kind="text", value=f"valid to {data['value']}"))
                    ctx.add_finding(self._finding(check, url, [f"Open {url}", "Inspect the TLS connection"], ev,
                                                  actual=data["observed"]))
                if not found:
                    ctx.passed(self.name, 2)
        self._cookies(target, https, ctx)
        self._aggregate(st.get("hits", {}), len(audited), int(cfg.get("max_pages_listed", 10)), ctx)

    def _http_redirect(self, target: str, ctx) -> None:
        parts = urlsplit(target)
        http_url = urlunsplit(("http", parts.hostname or "", parts.path or "/", parts.query, ""))
        try:
            resp = ctx.session.request().get(http_url, max_redirects=0,
                                             timeout=ctx.cfg["browser"]["timeout_ms"])
        except Exception as e:  # noqa: BLE001 - nothing listening on http:// is fine
            ctx.log.info("http:// probe of %s: %s", http_url, str(e).splitlines()[0] if str(e) else e)
            ctx.passed(self.name)
            return
        location = resp.headers.get("location", "")
        if 300 <= resp.status < 400 and location.startswith("https://"):
            ctx.passed(self.name)
            return
        observed = f"GET {http_url} -> HTTP {resp.status}" + (f", Location: {location}" if location else "")
        ctx.add_finding(self._finding("http-not-redirected", http_url, [f"Request {http_url}",
                                      "Look at the status and Location header"],
                                      [Evidence(kind="header", value=observed)], actual=observed))

    def _cookies(self, target: str, https: bool, ctx) -> None:
        host = urlsplit(target).hostname or ""
        try:
            cookies = ctx.session.context.cookies()
        except Exception as e:  # noqa: BLE001
            ctx.log.warning("cookies unavailable: %s", e)
            return
        # names and flags only: the values are dropped here and never stored
        cookies = [{k: c.get(k) for k in ("name", "domain", "secure", "httpOnly", "sameSite")} for c in cookies]
        declared = getattr(ctx.session, "declared_cookies", {})
        for c in cookies:
            c["declared"] = declared.get(c["name"])
        found = checks.cookie_findings(cookies, host, https)
        by_check: dict[str, list[dict]] = {}
        for check, data in found:
            by_check.setdefault(check, []).append(data)
        own = [c for c in cookies if checks.own_cookie(c, host)]
        if own and not found:
            ctx.passed(self.name, len(own))
        for check, items in by_check.items():
            names = ", ".join(i["cookie"] for i in items)
            ev = [Evidence(kind="text", value=f"cookie {i['cookie']}: {i['observed']}") for i in items[:10]]
            ctx.add_finding(self._finding(check, target, [f"Open {target}", "Inspect the cookies in the browser",
                                                          f"Look at the flags of: {names}"], ev,
                                          actual=f"cookie(s): {names}"))

    def _aggregate(self, hits: dict, n_pages: int, max_listed: int, ctx) -> None:
        for check, items in hits.items():
            url0, first = items[0]
            pages = [u for u, _ in items]
            listed = ", ".join(pages[:max_listed]) + (f" (+{len(pages) - max_listed} more)"
                                                      if len(pages) > max_listed else "")
            ev = [Evidence(kind="header", value=f"{u}: {d['observed']}") for u, d in items[:3]]
            ev.append(Evidence(kind="header", value=first["seen"], label=url0))
            for u, d in items[:3]:
                for ref in d.get("items", [])[:5]:
                    ev.append(Evidence(kind="html", value=f"<{ref['tag']}> {ref['url']}", label=u))
                if d.get("value"):
                    ev.append(Evidence(kind="header", value=d["value"], label=u))
            f = self._finding(check, url0, [f"Open {url0}", "Inspect the response headers and page source"], ev,
                              actual=f"{first['observed']} — on {len(pages)} of {n_pages} audited page(s)")
            f.description = f"Affected pages: {listed}"
            if check == "clickjacking" and any(d["password"] for _, d in items):
                f.severity = "medium"
                f.description += ". At least one of them has a password field."
            ctx.add_finding(f)

    def _finding(self, check, url, steps, evidence, actual="") -> Finding:
        title, status, severity, confidence, expected, recommendation, refs = RULES[check]
        return Finding(module=self.name, check=check, title=title, status=status, severity=severity,
                       confidence=confidence, url=url, expected=expected, actual=actual,
                       recommendation=recommendation, steps=steps, evidence=evidence, references=list(refs))


MODULE = Security()
