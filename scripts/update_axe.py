"""Update the axe-core bundled with qa-audit (docs/qa-audit.md §14).

    python scripts/update_axe.py 4.13.0

Downloads the npm tarball, checks npm's integrity hash (sha512, from the registry metadata), extracts
axe.min.js and LICENSE into src/qa_audit/modules/accessibility/vendor/ and rewrites axe.yaml with the version,
source, integrity and the SHA-256 of axe.min.js. tests/unit/test_engine_guards.py checks they agree.

Development-time only: the engine itself never downloads anything. Exit 0 ok · 1 verification failed · 2 usage.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "src", "qa_audit", "modules", "accessibility", "vendor")
REGISTRY = "https://registry.npmjs.org/axe-core/"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not re.fullmatch(r"\d+\.\d+\.\d+", argv[0]):
        print(__doc__)
        return 2
    version = argv[0]
    meta = json.loads(fetch(REGISTRY + version))
    tarball, integrity = meta["dist"]["tarball"], meta["dist"]["integrity"]
    algo, _, expected = integrity.partition("-")
    if algo != "sha512":
        print(f"unexpected integrity algorithm {algo}")
        return 1
    data = fetch(tarball)
    actual = base64.b64encode(hashlib.sha512(data).digest()).decode()
    if actual != expected:
        print(f"integrity mismatch for {tarball}")
        return 1
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        axe = tar.extractfile("package/axe.min.js").read()
        license_text = tar.extractfile("package/LICENSE").read()
    if f"axe v{version}".encode() not in axe[:500] and f'version:"{version}"'.encode() not in axe:
        print("the extracted axe.min.js does not declare the requested version")
        return 1
    os.makedirs(VENDOR, exist_ok=True)
    with open(os.path.join(VENDOR, "axe.min.js"), "wb") as f:
        f.write(axe)
    with open(os.path.join(VENDOR, "LICENSE-axe-core.txt"), "wb") as f:
        f.write(license_text)
    sha256 = hashlib.sha256(axe).hexdigest()
    # both checksums are public (npm registry; this file): marked for detect-secrets on their own lines only
    pragma = "  # pragma: " + "allowlist secret"   # split: only the manifest lines carry the marker
    with open(os.path.join(VENDOR, "axe.yaml"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# written by scripts/update_axe.py; checked by tests/unit/test_engine_guards.py\n"
                f"package: axe-core\nversion: {version}\nlicense: {meta.get('license', '')}\n"
                f"source: {tarball}\n"
                f"npm_integrity: {integrity}{pragma}\n"
                f"sha256: {sha256}{pragma}\n")
    print(f"axe-core {version} bundled ({len(axe)} bytes, sha256 {sha256[:12]}...)")
    return 0


if __name__ == "__main__":
    # on Windows, output read through a pipe defaulted to cp1252 (same fix as the other scripts)
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
