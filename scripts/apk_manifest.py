"""
Reads an APK's AndroidManifest.xml (binary XML, "AXML") in pure Python, without aapt2 (plan 11.1): the package,
versionName, versionCode, minSdkVersion and targetSdkVersion. Shared by scripts/workspace_build.py and
scripts/setup_emulator.py (which reinstalled the build on every run when aapt2 was missing, losing the app's login).

    from apk_manifest import read_manifest
    read_manifest("app.apk")   # {"package": "com.demoshop.app", "version_name": "1.4.0", "version_code": 140, ...}

The format (the chunks of Android's ResourceTypes.h): a file header (type 0x0003), a string pool (0x0001: UTF-8 when
flag 0x100 is set, else UTF-16, lengths of 1 or 2 units), an optional resource-id map (0x0180) and the XML tree
chunks; for START_ELEMENT (0x0102) the attributes are 20 bytes each: namespace, name, raw value (string index or
0xFFFFFFFF), then a Res_value: size (u16), res0 (u8), dataType (u8), data (u32). An attribute whose name was
stripped from the string pool (shrunk APKs) is found by its resource id in the map.

Read only; the APK is never changed. ManifestError when the file is not an APK or the manifest cannot be read.
"""

from __future__ import annotations

import struct
import zipfile

RES_XML_TYPE, RES_STRING_POOL_TYPE, RES_XML_RESOURCE_MAP_TYPE = 0x0003, 0x0001, 0x0180
RES_XML_START_ELEMENT_TYPE = 0x0102
UTF8_FLAG = 0x100
NO_INDEX = 0xFFFFFFFF
TYPE_STRING, TYPE_INT_DEC, TYPE_INT_HEX = 0x03, 0x10, 0x11
# android:<attribute> resource ids (android.R.attr), for attributes whose name is not in the string pool
ATTRIBUTE_IDS = {0x0101021B: "versionCode", 0x0101021C: "versionName", 0x0101020C: "minSdkVersion",
                 0x01010270: "targetSdkVersion"}


class ManifestError(ValueError):
    """Not an APK, or its manifest is not readable binary XML."""


def _u16(data: bytes, at: int) -> int:
    return struct.unpack_from("<H", data, at)[0]


def _u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def string_pool(data: bytes, start: int) -> list[str]:
    """The strings of the pool chunk that starts at `start`."""
    header_size, count, flags, strings_start = _u16(data, start + 2), _u32(data, start + 8), \
        _u32(data, start + 16), _u32(data, start + 20)
    utf8 = bool(flags & UTF8_FLAG)
    offsets = [_u32(data, start + header_size + 4 * i) for i in range(count)]
    out = []
    for off in offsets:
        at = start + strings_start + off
        if utf8:
            at += 2 if data[at] & 0x80 else 1                      # length in characters (not needed)
            n = data[at]
            if n & 0x80:
                n, at = ((n & 0x7F) << 8) | data[at + 1], at + 2
            else:
                at += 1
            out.append(data[at:at + n].decode("utf-8", errors="replace"))
        else:
            n = _u16(data, at)
            if n & 0x8000:
                n, at = ((n & 0x7FFF) << 16) | _u16(data, at + 2), at + 4
            else:
                at += 2
            out.append(data[at:at + 2 * n].decode("utf-16-le", errors="replace"))
    return out


def parse_axml(data: bytes) -> dict:
    """{element name: {attribute name: value}} for the first `manifest` and `uses-sdk` elements; an int value
    for integers, a text for strings."""
    if len(data) < 8 or _u16(data, 0) != RES_XML_TYPE:
        raise ManifestError("AndroidManifest.xml is not binary XML (AXML)")
    strings: list[str] = []
    ids: list[int] = []
    found: dict[str, dict] = {}
    at, end = _u16(data, 2), min(len(data), _u32(data, 4))
    while at + 8 <= end:
        kind, header_size, size = _u16(data, at), _u16(data, at + 2), _u32(data, at + 4)
        if size < 8 or at + size > end:
            raise ManifestError(f"a broken chunk (type 0x{kind:04x}) at byte {at}")
        if kind == RES_STRING_POOL_TYPE and not strings:
            strings = string_pool(data, at)
        elif kind == RES_XML_RESOURCE_MAP_TYPE:
            ids = [_u32(data, at + header_size + 4 * i) for i in range((size - header_size) // 4)]
        elif kind == RES_XML_START_ELEMENT_TYPE:
            ext = at + header_size
            name_index = _u32(data, ext + 4)
            element = strings[name_index] if name_index < len(strings) else ""
            if element in ("manifest", "uses-sdk") and element not in found:
                attr_start, attr_size, count = _u16(data, ext + 8), _u16(data, ext + 10), _u16(data, ext + 12)
                attributes = {}
                for i in range(count):
                    a = ext + attr_start + i * (attr_size or 20)
                    name_i, raw, data_type, value = _u32(data, a + 4), _u32(data, a + 8), data[a + 15], \
                        _u32(data, a + 16)
                    name = strings[name_i] if name_i < len(strings) else ""
                    if not name and name_i < len(ids):
                        name = ATTRIBUTE_IDS.get(ids[name_i], "")
                    if not name:
                        continue
                    if data_type == TYPE_STRING and value < len(strings):
                        attributes[name] = strings[value]
                    elif data_type in (TYPE_INT_DEC, TYPE_INT_HEX):
                        attributes[name] = value
                    elif raw != NO_INDEX and raw < len(strings):
                        attributes[name] = strings[raw]
                found[element] = attributes
        at += size
    if "manifest" not in found:
        raise ManifestError("no <manifest> element in AndroidManifest.xml")
    return found


def read_manifest(apk: str) -> dict:
    """package, version_name, version_code (int or None), min_sdk, target_sdk of an APK."""
    try:
        with zipfile.ZipFile(apk) as z:
            data = z.read("AndroidManifest.xml")
    except (zipfile.BadZipFile, OSError) as e:
        raise ManifestError(f"{apk}: not an APK ({e})") from None
    except KeyError:
        raise ManifestError(f"{apk}: no AndroidManifest.xml inside") from None
    try:
        tree = parse_axml(data)
    except (struct.error, IndexError) as e:
        raise ManifestError(f"AndroidManifest.xml could not be read ({e})") from None
    m, sdk = tree["manifest"], tree.get("uses-sdk", {})
    code = m.get("versionCode")
    if not m.get("package"):
        raise ManifestError("the manifest has no package")
    return {"package": str(m["package"]), "version_name": None if m.get("versionName") is None else
            str(m["versionName"]), "version_code": code if isinstance(code, int) else None,
            "min_sdk": sdk.get("minSdkVersion"), "target_sdk": sdk.get("targetSdkVersion")}
