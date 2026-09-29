"""Fictional APKs for the tests (plans 9.2 and 11.1): a zip with a hand-built binary AndroidManifest.xml (AXML) and,
optionally, native libraries. Never a real app: the Demo Shop's package and made-up versions.

    make_apk(path, package="com.demoshop.app", version_name="1.4.0", version_code=140, libs=..., stored=True)
"""

import struct
import zipfile

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ATTR_IDS = [("versionCode", 0x0101021B), ("versionName", 0x0101021C), ("minSdkVersion", 0x0101020C),
            ("targetSdkVersion", 0x01010270)]
NO_INDEX = 0xFFFFFFFF


def _len8(n: int) -> bytes:
    return bytes([n]) if n < 0x80 else bytes([0x80 | (n >> 8), n & 0xFF])


def _string_pool(strings: list[str], utf8: bool) -> bytes:
    data, offsets = b"", []
    for s in strings:
        offsets.append(len(data))
        if utf8:
            raw = s.encode("utf-8")
            data += _len8(len(s)) + _len8(len(raw)) + raw + b"\0"
        else:
            raw = s.encode("utf-16-le")
            n = len(raw) // 2
            data += (struct.pack("<H", n) if n < 0x8000 else struct.pack("<HH", 0x8000 | (n >> 16), n & 0xFFFF))
            data += raw + b"\0\0"
    data += b"\0" * (-len(data) % 4)
    header_size = 28
    strings_start = header_size + 4 * len(strings)
    size = strings_start + len(data)
    return (struct.pack("<HHIIIIII", 0x0001, header_size, size, len(strings), 0, 0x100 if utf8 else 0,
                        strings_start, 0) + b"".join(struct.pack("<I", o) for o in offsets) + data)


def axml(package="com.demoshop.app", version_name="1.4.0", version_code=140, min_sdk=24, target_sdk=36,
         utf8=True, strip_names=False, code_as_hex=False) -> bytes:
    """A binary AndroidManifest.xml. strip_names: the android attribute names are empty in the string pool (a
    shrunk APK), found only through the resource map."""
    names = ["" if strip_names else n for n, _ in ATTR_IDS]
    strings = [*names, "package", "android", ANDROID_NS, "manifest", "uses-sdk", package]
    if version_name is not None:
        strings.append(version_name)
    idx = {s: i for i, s in enumerate(strings) if s}
    ns = idx[ANDROID_NS]

    def attr(name_i, data_type, data, raw=NO_INDEX, namespace=ns):
        return struct.pack("<IIIHBBI", namespace, name_i, raw, 8, 0, data_type, data)

    def element(name, attrs):
        body = struct.pack("<IIHHHHHH", NO_INDEX, idx[name], 20, 20, len(attrs), 0, 0, 0) + b"".join(attrs)
        start = struct.pack("<HHIII", 0x0102, 16, 16 + len(body), 1, NO_INDEX) + body
        end = struct.pack("<HHIIIII", 0x0103, 16, 24, 1, NO_INDEX, NO_INDEX, idx[name])
        return start, end

    manifest_attrs = [attr(idx["package"], 0x03, idx[package], raw=idx[package], namespace=NO_INDEX)]
    if version_code is not None:
        manifest_attrs.insert(0, attr(0, 0x11 if code_as_hex else 0x10, version_code))
    if version_name is not None:
        manifest_attrs.append(attr(1, 0x03, idx[version_name], raw=idx[version_name]))
    m_start, m_end = element("manifest", manifest_attrs)
    s_start, s_end = element("uses-sdk", [attr(2, 0x10, min_sdk), attr(3, 0x10, target_sdk)])
    ns_start = struct.pack("<HHIIIII", 0x0100, 16, 24, 1, NO_INDEX, idx["android"], ns)
    ns_end = struct.pack("<HHIIIII", 0x0101, 16, 24, 1, NO_INDEX, idx["android"], ns)
    res_map = struct.pack("<HHI", 0x0180, 8, 8 + 4 * len(ATTR_IDS)) + b"".join(struct.pack("<I", i)
                                                                               for _, i in ATTR_IDS)
    body = _string_pool(strings, utf8) + res_map + ns_start + m_start + s_start + s_end + m_end + ns_end
    return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body


def make_apk(path, libs=(("arm64-v8a", "libfoo.so"), ("arm64-v8a", "libbar.so")), stored=True, manifest=None,
             **manifest_args):
    """A fictional APK: a binary manifest (or the bytes given) and native libraries (stored = uncompressed, as with
    extractNativeLibs=false)."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", manifest if manifest is not None else axml(**manifest_args),
                   compress_type=zipfile.ZIP_DEFLATED)
        for abi, name in libs:
            z.writestr(f"lib/{abi}/{name}", b"\x7fELF fictional " + name.encode() * 20,
                       compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
    return path
