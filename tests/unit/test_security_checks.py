"""Passive security checks as pure functions (docs/qa-audit.md §16). No browser, no network."""

import unittest

from qa_audit.modules.security import checks

STRICT = {"content-security-policy": "default-src 'self'; frame-ancestors 'self'",
          "x-frame-options": "DENY", "x-content-type-options": "nosniff",
          "strict-transport-security": "max-age=31536000; includeSubDomains", "server": "nginx"}


class Headers(unittest.TestCase):
    def test_strict_headers_pass_everything(self):
        self.assertIsNone(checks.csp_missing(STRICT, []))
        self.assertIsNone(checks.csp_unsafe(STRICT, []))
        self.assertIsNone(checks.clickjacking(STRICT, []))
        self.assertIsNone(checks.nosniff_missing(STRICT))
        self.assertIsNone(checks.hsts(STRICT, 15552000))
        self.assertIsNone(checks.version_disclosure(STRICT))     # "nginx" without a version is fine

    def test_missing_headers(self):
        self.assertIsNotNone(checks.csp_missing({}, []))
        self.assertIsNotNone(checks.clickjacking({}, []))
        self.assertIsNotNone(checks.nosniff_missing({"x-content-type-options": "sniff"}))
        self.assertEqual(checks.hsts({}, 100)[0], "hsts-missing")

    def test_csp_in_a_meta_tag_counts_but_frame_ancestors_there_does_not(self):
        meta = ["default-src 'self'; frame-ancestors 'none'"]
        self.assertIsNone(checks.csp_missing({}, meta))
        self.assertIsNotNone(checks.clickjacking({}, meta))      # browsers ignore frame-ancestors in <meta>
        self.assertIsNone(checks.clickjacking({"x-frame-options": "SAMEORIGIN"}, []))
        self.assertIsNotNone(checks.clickjacking({"x-frame-options": "ALLOW-FROM https://a.example"}, []))

    def test_unsafe_csp(self):
        cases = {
            "script-src 'self' 'unsafe-inline'": "'unsafe-inline'",
            "default-src 'self' 'unsafe-eval'": "'unsafe-eval'",
            "script-src *": "*",
        }
        for policy, expected in cases.items():
            with self.subTest(policy=policy):
                r = checks.csp_unsafe({"content-security-policy": policy}, [])
                self.assertIn(expected, r["observed"])
        # a nonce or hash makes browsers ignore 'unsafe-inline' (CSP level 2+)
        self.assertIsNone(checks.csp_unsafe({"content-security-policy": "script-src 'unsafe-inline' 'nonce-abc'"}, []))
        self.assertIsNone(checks.csp_unsafe({"content-security-policy": "img-src *"}, []))   # not scripts

    def test_short_hsts(self):
        check, info = checks.hsts({"strict-transport-security": "max-age=300"}, 15552000)
        self.assertEqual(check, "hsts-short")
        self.assertIn("max-age=300", info["observed"])

    def test_version_disclosure(self):
        r = checks.version_disclosure({"server": "Apache/2.4.1 (Unix)", "x-powered-by": "PHP/5.6.40"})
        self.assertIn("server: Apache/2.4.1", r["observed"])
        self.assertIn("x-powered-by: PHP/5.6.40", r["observed"])


class Content(unittest.TestCase):
    def test_mixed_content_split(self):
        active, passive = checks.insecure_references([{"tag": "script", "url": "http://a", "active": True},
                                                      {"tag": "img", "url": "http://b", "active": False}])
        self.assertEqual((len(active), len(passive)), (1, 1))

    def test_forms_to_http(self):
        forms = [{"action": "http://shop.example/login"}, {"action": "https://shop.example/search"}]
        self.assertEqual(checks.forms_to_http(forms), ["http://shop.example/login"])


class Cookies(unittest.TestCase):
    def cookie(self, **kw):
        base = {"name": "theme", "domain": "shop.example", "secure": True, "httpOnly": True, "sameSite": "Lax"}
        base.update(kw)
        return base

    def test_flags(self):
        found = checks.cookie_findings([
            self.cookie(name="sessionid", httpOnly=False),
            self.cookie(name="prefs", secure=False),
            self.cookie(name="track", sameSite="None", secure=False),
            self.cookie(name="ok"),
        ], "shop.example", https=True)
        self.assertIn(("cookie-session-not-httponly", "sessionid"), [(c, d["cookie"]) for c, d in found])
        self.assertIn(("cookie-not-secure", "prefs"), [(c, d["cookie"]) for c, d in found])
        self.assertIn(("cookie-samesite-none-insecure", "track"), [(c, d["cookie"]) for c, d in found])
        self.assertNotIn("ok", [d["cookie"] for _, d in found])

    def test_http_site_is_not_blamed_for_missing_secure(self):
        found = checks.cookie_findings([self.cookie(secure=False)], "shop.example", https=False)
        self.assertEqual(found, [])

    def test_only_own_cookies(self):
        self.assertTrue(checks.own_cookie(self.cookie(domain=".shop.example"), "www.shop.example"))
        self.assertFalse(checks.own_cookie(self.cookie(domain="ads.example"), "shop.example"))
        found = checks.cookie_findings([self.cookie(domain="ads.example", secure=False)], "shop.example", True)
        self.assertEqual(found, [])

    def test_values_never_in_the_findings(self):
        c = self.cookie(name="sessionid", httpOnly=False)
        c["value"] = "s3cr3t-value"
        found = checks.cookie_findings([c], "shop.example", True)
        self.assertNotIn("s3cr3t-value", repr(found))


class Tls(unittest.TestCase):
    def test_old_protocol_and_expiry(self):
        now = 1_800_000_000
        found = dict(checks.tls_findings({"protocol": "TLS 1.1", "validTo": now + 3 * 86400,
                                          "subjectName": "shop.example"}, 14, now=now))
        self.assertIn("tls-old-protocol", found)
        self.assertIn("3.0 days", found["tls-cert-expiring"]["observed"])

    def test_modern_and_valid(self):
        now = 1_800_000_000
        self.assertEqual(checks.tls_findings({"protocol": "TLS 1.3", "validTo": now + 90 * 86400}, 14, now=now), [])
        self.assertEqual(checks.tls_findings({}, 14), [])


if __name__ == "__main__":
    unittest.main()
