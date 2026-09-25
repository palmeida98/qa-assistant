"""Image comparison and baseline storage (docs/qa-audit.md §19). Pillow is imported lazily."""

from __future__ import annotations

import os
import re
import shutil
from urllib.parse import urlsplit

GITIGNORE = "# written by qa-audit: visual baselines are screenshots of audited sites, never committed\n*\n"


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


# --- baselines -------------------------------------------------------------------------------------------------
def default_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".qa-audit", "baselines")


def root(cfg_dir: str | None) -> str:
    return os.path.abspath(cfg_dir) if cfg_dir else default_root()


def ensure_root(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    gi = os.path.join(path, ".gitignore")
    if not os.path.isfile(gi):
        with open(gi, "w", encoding="utf-8") as f:
            f.write(GITIGNORE)


def _safe(text: str, limit: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:limit] or "root"


def host_key(url: str) -> str:
    p = urlsplit(url)
    return _safe(f"{p.hostname}_{p.port}" if p.port else (p.hostname or "unknown"))


def page_key(url: str) -> str:
    p = urlsplit(url)
    key = p.path.strip("/") or "index"
    if p.query:
        key += "_" + p.query
    return _safe(key, 120)


def baseline_path(base_root: str, url: str, engine: str, viewport: str) -> str:
    return os.path.join(base_root, host_key(url), _safe(engine), _safe(viewport), page_key(url) + ".png")


def list_baselines(base_root: str) -> list[tuple[str, int, int]]:
    """[(host, files, bytes)]"""
    out = []
    if not os.path.isdir(base_root):
        return out
    for host in sorted(os.listdir(base_root)):
        d = os.path.join(base_root, host)
        if not os.path.isdir(d):
            continue
        files = [os.path.join(dp, f) for dp, _, fs in os.walk(d) for f in fs if f.endswith(".png")]
        out.append((host, len(files), sum(os.path.getsize(f) for f in files)))
    return out


def delete_baselines(base_root: str, host: str) -> list[str]:
    """Delete one host's baselines (the name as listed) or all of them; returns what was removed."""
    removed = []
    for name, _, _ in list_baselines(base_root):
        if host == "all" or name == host:
            shutil.rmtree(os.path.join(base_root, name))
            removed.append(name)
    return removed


# --- comparison ------------------------------------------------------------------------------------------------
def crop_height(path: str, max_height: int) -> bool:
    """Cut a screenshot taller than max_height; True when it was cut."""
    from PIL import Image

    with Image.open(path) as im:
        if im.height <= max_height:
            return False
        im.crop((0, 0, im.width, max_height)).save(path)
    return True


def compare(baseline: str, current: str, tolerance: int, diff_path: str | None = None) -> dict:
    """Share of changed pixels over the common area, size change, and an optional diff image."""
    from PIL import Image, ImageChops

    with Image.open(baseline) as b0, Image.open(current) as c0:
        b, c = b0.convert("RGB"), c0.convert("RGB")
    size_changed = b.size != c.size
    w, h = min(b.width, c.width), min(b.height, c.height)
    bc, cc = b.crop((0, 0, w, h)), c.crop((0, 0, w, h))
    diff = ImageChops.difference(bc, cc)
    # a pixel changed when any channel moved more than the tolerance
    channels = [ch.point(lambda v: 255 if v > tolerance else 0) for ch in diff.split()]
    mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    changed = mask.histogram()[255]
    total = max(w * h, 1)
    # pixels outside the common area count as changed when the size differs
    extra = max(b.width * b.height, c.width * c.height) - w * h
    ratio = (changed + extra) / max(total + extra, 1)
    if diff_path and (changed or size_changed):
        faded = Image.blend(bc, Image.new("RGB", bc.size, (255, 255, 255)), 0.7)
        red = Image.new("RGB", bc.size, (230, 0, 0))
        faded.paste(red, (0, 0), mask)
        faded.save(diff_path)
    return {"ratio": ratio, "changed_pixels": changed, "size_changed": size_changed,
            "baseline_size": b.size, "current_size": c.size}
