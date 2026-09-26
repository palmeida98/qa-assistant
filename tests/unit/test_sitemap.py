"""Sitemap parsing and collection (docs/qa-audit.md §5.1). No browser, no network: fetch is a dict."""

import unittest

from qa_audit.browser import sitemap

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
SITE = "https://shop.example"


def urlset(*locs) -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8"?><urlset {NS}>'
            + "".join(f"<url><loc>{u}</loc><lastmod>2026-09-01</lastmod></url>" for u in locs)
            + "</urlset>").encode()


def index(*locs) -> bytes:
    return (f'<?xml version="1.0"?><sitemapindex {NS}>'
            + "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in locs) + "</sitemapindex>").encode()


def fetcher(files: dict):
    asked = []

    def fetch(url):
        asked.append(url)
        if url not in files:
            return 404, url, b"not found"
        return 200, url, files[url]
    return fetch, asked


class Parse(unittest.TestCase):
    def test_urlset_and_index(self):
        self.assertEqual(sitemap.parse(urlset(SITE + "/a", SITE + "/b")), ("urlset", [SITE + "/a", SITE + "/b"]))
        self.assertEqual(sitemap.parse(index(SITE + "/s1.xml")), ("sitemapindex", [SITE + "/s1.xml"]))

    def test_without_namespace_and_with_blank_locs(self):
        self.assertEqual(sitemap.parse(b"<urlset><url><loc> /a </loc></url><url><loc></loc></url></urlset>"),
                         ("urlset", ["/a"]))

    def test_entity_declarations_are_refused_before_parsing(self):
        bomb = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
                b"<urlset><url><loc>&lol2;</loc></url></urlset>")
        external = b'<!DOCTYPE urlset [<!ENTITY x SYSTEM "file:///etc/passwd">]><urlset/>'
        for body in (bomb, external, b"<!doctype html><html></html>"):
            with self.subTest(body=body[:30]), self.assertRaisesRegex(sitemap.SitemapError, "refused"):
                sitemap.parse(body)

    def test_not_a_sitemap(self):
        for body, why in ((b"<html><body>hi</body></html>", "not a sitemap"), (b"{not xml", "not XML")):
            with self.subTest(why=why), self.assertRaisesRegex(sitemap.SitemapError, why):
                sitemap.parse(body)


class Collect(unittest.TestCase):
    def test_robots_order_one_level_of_index(self):
        fetch, asked = fetcher({SITE + "/sitemap.xml": index(SITE + "/s1.xml", SITE + "/s2.xml"),
                                SITE + "/s1.xml": urlset(SITE + "/a", SITE + "/b"),
                                SITE + "/s2.xml": urlset(SITE + "/c")})
        urls, status, skipped = sitemap.collect(fetch, [SITE + "/sitemap.xml"], SITE)
        self.assertEqual(asked, [SITE + "/sitemap.xml", SITE + "/s1.xml", SITE + "/s2.xml"])
        self.assertEqual(urls, [SITE + "/a", SITE + "/b", SITE + "/c"])
        self.assertEqual(status, "read: 3 URLs from /sitemap.xml, /s1.xml, /s2.xml")
        self.assertEqual(skipped, [])

    def test_never_fetches_outside_the_origin_or_compressed_files(self):
        fetch, asked = fetcher({SITE + "/sitemap.xml": index("https://cdn.example/s.xml", SITE + "/s.xml.gz")})
        urls, _, skipped = sitemap.collect(fetch, [SITE + "/sitemap.xml", "https://other.example/s.xml"], SITE)
        self.assertEqual(asked, [SITE + "/sitemap.xml"])
        self.assertEqual(urls, [])
        self.assertEqual({s["reason"] for s in skipped},
                         {"sitemap outside the target origin", "compressed sitemap (.gz) not supported"})

    def test_nested_index_ignored(self):
        fetch, asked = fetcher({SITE + "/sitemap.xml": index(SITE + "/i2.xml"), SITE + "/i2.xml": index(SITE + "/s.xml")})
        urls, status, _ = sitemap.collect(fetch, [SITE + "/sitemap.xml"], SITE)
        self.assertNotIn(SITE + "/s.xml", asked)
        self.assertEqual(urls, [])
        self.assertIn("nested sitemap index ignored", status)

    def test_limits(self):
        many = [f"{SITE}/p{i}" for i in range(sitemap.MAX_URLS + 5)]
        fetch, _ = fetcher({SITE + "/sitemap.xml": urlset(*many)})
        urls, status, _ = sitemap.collect(fetch, [SITE + "/sitemap.xml"], SITE)
        self.assertEqual(len(urls), sitemap.MAX_URLS)
        self.assertIn("5 URLs beyond the limit", status)
        files = [f"{SITE}/s{i}.xml" for i in range(sitemap.MAX_FILES + 2)]
        fetch, asked = fetcher({SITE + "/sitemap.xml": index(*files)})
        _, _, skipped = sitemap.collect(fetch, [SITE + "/sitemap.xml"], SITE)
        self.assertEqual(len(asked), sitemap.MAX_FILES)
        self.assertEqual(sum(1 for s in skipped if "sitemap files" in s["reason"]), 3)
        big = {SITE + "/sitemap.xml": b" " * (sitemap.MAX_BYTES + 1)}
        self.assertIn("larger than", sitemap.collect(fetcher(big)[0], [SITE + "/sitemap.xml"], SITE)[1])

    def test_missing_refused_or_redirected_elsewhere_never_stops(self):
        fetch, _ = fetcher({})
        self.assertEqual(sitemap.collect(fetch, [SITE + "/sitemap.xml"], SITE)[1], "/sitemap.xml: none (HTTP 404)")

        def elsewhere(url):
            return 200, "https://login.example/sso", urlset(SITE + "/a")
        self.assertIn("redirected outside", sitemap.collect(elsewhere, [SITE + "/sitemap.xml"], SITE)[1])

        def broken(url):
            raise TimeoutError
        self.assertIn("unavailable (TimeoutError)", sitemap.collect(broken, [SITE + "/sitemap.xml"], SITE)[1])


if __name__ == "__main__":
    unittest.main()
