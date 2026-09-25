"""Visual comparison and baseline storage (docs/qa-audit.md §19). Pillow, no browser."""

import os
import tempfile
import unittest

from qa_audit.modules.visual import compare
from qa_audit.modules.visual.module import MODULE

try:
    from PIL import Image
except ImportError:  # pragma: no cover - the visual extra is installed in CI
    Image = None


@unittest.skipIf(Image is None, "Pillow not installed (pip install -e .[visual])")
class Compare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def img(self, name, size=(100, 100), color=(255, 255, 255), block=None):
        im = Image.new("RGB", size, color)
        if block:
            box, fill = block
            im.paste(Image.new("RGB", (box[2] - box[0], box[3] - box[1]), fill), box[:2])
        path = os.path.join(self.tmp.name, name)
        im.save(path)
        return path

    def test_identical(self):
        r = compare.compare(self.img("a.png"), self.img("b.png"), 16, os.path.join(self.tmp.name, "d.png"))
        self.assertEqual((r["ratio"], r["size_changed"]), (0, False))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "d.png")))   # no diff image when equal

    def test_changed_block_and_diff_image(self):
        diff = os.path.join(self.tmp.name, "d.png")
        r = compare.compare(self.img("a.png"), self.img("b.png", block=((0, 0, 10, 10), (0, 0, 0))), 16, diff)
        self.assertAlmostEqual(r["ratio"], 0.01)
        self.assertEqual(r["changed_pixels"], 100)
        with Image.open(diff) as d:
            self.assertEqual(d.getpixel((5, 5)), (230, 0, 0))          # changed pixels in red
            self.assertNotEqual(d.getpixel((50, 50)), (230, 0, 0))

    def test_tolerance_ignores_tiny_changes(self):
        r = compare.compare(self.img("a.png"), self.img("b.png", color=(250, 250, 250)), 16)
        self.assertEqual(r["ratio"], 0)

    def test_size_change_counts_the_extra_area(self):
        r = compare.compare(self.img("a.png"), self.img("b.png", size=(100, 200)), 16)
        self.assertTrue(r["size_changed"])
        self.assertAlmostEqual(r["ratio"], 0.5)

    def test_crop_height(self):
        path = self.img("tall.png", size=(10, 500))
        self.assertTrue(compare.crop_height(path, 100))
        with Image.open(path) as im:
            self.assertEqual(im.size, (10, 100))
        self.assertFalse(compare.crop_height(path, 100))


class Baselines(unittest.TestCase):
    def test_paths_are_safe_and_per_host_engine_viewport(self):
        p = compare.baseline_path("/b", "http://127.0.0.1:8000/shop/item?id=3&x=<y>", "chromium", "mobile")
        self.assertEqual(os.path.normpath(p), os.path.normpath("/b/127.0.0.1_8000/chromium/mobile/shop-item_id-3-x-y.png"))
        self.assertTrue(compare.baseline_path("/b", "https://shop.example/", "firefox", "desktop")
                        .replace("\\", "/").endswith("shop.example/firefox/desktop/index.png"))

    def test_default_root_is_outside_the_repository_and_protected(self):
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertFalse(os.path.abspath(compare.default_root()).startswith(repo))
        with tempfile.TemporaryDirectory() as tmp:
            compare.ensure_root(tmp)
            with open(os.path.join(tmp, ".gitignore"), encoding="utf-8") as f:
                self.assertIn("*", f.read().split())

    def test_list_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            for host in ("a.example", "b.example"):
                d = os.path.join(tmp, host, "chromium", "desktop")
                os.makedirs(d)
                with open(os.path.join(d, "index.png"), "wb") as f:
                    f.write(b"x" * 10)
            self.assertEqual(compare.list_baselines(tmp), [("a.example", 1, 10), ("b.example", 1, 10)])
            self.assertEqual(compare.delete_baselines(tmp, "a.example"), ["a.example"])
            self.assertEqual(compare.delete_baselines(tmp, "all"), ["b.example"])
            self.assertEqual(compare.list_baselines(tmp), [])


class Configuration(unittest.TestCase):
    def test_defaults_and_validation(self):
        cfg = MODULE.default_config()
        self.assertEqual([v["name"] for v in cfg["viewports"]], ["desktop", "tablet", "mobile"])
        self.assertIsNone(cfg["baseline_dir"])
        self.assertEqual(MODULE.validate_config(cfg), [])
        bad = {"viewports": [{"name": "x", "width": "wide"}], "max_diff_ratio": 2, "tolerance": 300}
        self.assertEqual(len(MODULE.validate_config(bad)), 3)


if __name__ == "__main__":
    unittest.main()
