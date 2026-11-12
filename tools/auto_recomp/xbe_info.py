"""Minimal, dependency-free reader for the original Xbox XBE header.

Mirrors the layout parsed by runtime/xbe/xbe.h, just enough to let the
automated workflow name a game, find its library build and sanity check a
file before handing it to xbe_symbols / xbe_recomp. Kept standalone (no C++
build required) so it can run as a pure Python step on any OS, including
inside a GitHub Actions job before the toolchain is even set up.
"""
from __future__ import annotations

import dataclasses
import struct
from pathlib import Path
from typing import Optional

RETAIL_ENTRY_KEY = 0xA8FC57AB
DEBUG_ENTRY_KEY = 0x94859D4B
RETAIL_THUNK_KEY = 0x5B6D40B6
DEBUG_THUNK_KEY = 0xEFB1F152


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


@dataclasses.dataclass
class Library:
    name: str
    major: int
    minor: int
    build: int
    flags: int


@dataclasses.dataclass
class XbeInfo:
    path: Path
    base: int
    header_size: int
    image_size: int
    entry: int
    debug: bool
    title_id: int
    title: str
    libraries: list

    @property
    def title_id_hex(self) -> str:
        return f"{self.title_id:08X}"

    def library_build(self, name_prefix: str) -> Optional[int]:
        """Highest build number of a library whose name starts with name_prefix
        (case-insensitive), e.g. "XAPI" or "D3D8". None of this XBE's libraries
        are guaranteed to be the one the SDK ships signatures for; callers
        should try every library and let xbe_symbols decide."""
        best = None
        for lib in self.libraries:
            if lib.name.upper().startswith(name_prefix.upper()):
                if best is None or lib.build > best:
                    best = lib.build
        return best


def parse_xbe(path: Path) -> XbeInfo:
    data = Path(path).read_bytes()
    if len(data) < 0x180 or data[0:4] != b"XBEH":
        raise ValueError(f"{path}: not an XBE (missing 'XBEH' magic)")

    base = _u32(data, 0x104)
    header_size = _u32(data, 0x108)
    image_size = _u32(data, 0x10C)
    cert = _u32(data, 0x118)
    nsec = _u32(data, 0x11C)
    ep = _u32(data, 0x128)
    kt = _u32(data, 0x158)

    def inside(v: int) -> bool:
        return base <= v < base + image_size

    debug = False
    ep_x = (ep ^ RETAIL_ENTRY_KEY) & 0xFFFFFFFF
    kt_x = (kt ^ RETAIL_THUNK_KEY) & 0xFFFFFFFF
    if not (inside(ep_x) and inside(kt_x)):
        ep_x = (ep ^ DEBUG_ENTRY_KEY) & 0xFFFFFFFF
        kt_x = (kt ^ DEBUG_THUNK_KEY) & 0xFFFFFFFF
        if inside(ep_x) and inside(kt_x):
            debug = True
        else:
            raise ValueError(f"{path}: entry point key not recognised (corrupt or not an Xbox XBE)")
    entry = ep_x

    nlib = _u32(data, 0x160)
    lib_addr = _u32(data, 0x164)
    libraries = []
    if lib_addr:
        lib_off = lib_addr - base
        for i in range(nlib):
            o = lib_off + i * 16
            if o + 16 > len(data):
                break
            name = data[o:o + 8].split(b"\x00", 1)[0].decode("ascii", "replace")
            major, minor, build, flags = struct.unpack_from("<HHHH", data, o + 8)
            libraries.append(Library(name, major, minor, build, flags))

    title = ""
    title_id = 0
    if cert and cert - base + 0x5C <= len(data):
        cert_off = cert - base
        title_id = _u32(data, cert_off + 0x08)
        raw_title = data[cert_off + 0x0C:cert_off + 0x0C + 80]
        try:
            title = raw_title.decode("utf-16-le").split("\x00", 1)[0].strip()
        except UnicodeDecodeError:
            title = ""

    return XbeInfo(
        path=Path(path),
        base=base,
        header_size=header_size,
        image_size=image_size,
        entry=entry,
        debug=debug,
        title_id=title_id,
        title=title,
        libraries=libraries,
    )


def slugify(name: str, fallback: str = "game") -> str:
    out = []
    prev_us = False
    for ch in name.strip():
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        elif not prev_us:
            out.append("_")
            prev_us = True
    slug = "".join(out).strip("_").lower()
    return slug or fallback


if __name__ == "__main__":
    import sys

    info = parse_xbe(Path(sys.argv[1]))
    print(f"title       : {info.title or '(none found)'}")
    print(f"title id    : {info.title_id_hex}")
    print(f"debug build : {info.debug}")
    print(f"libraries   :")
    for lib in info.libraries:
        print(f"  {lib.name:<16} {lib.major}.{lib.minor}.{lib.build}")
