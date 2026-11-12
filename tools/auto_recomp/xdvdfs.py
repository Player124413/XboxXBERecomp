"""Minimal, dependency-free XDVDFS (original Xbox disc filesystem) reader.

Just enough to find default.xbe inside an .iso so the automated workflow can
read its header, without requiring extract-xiso, xdvdfs-tools or any other
external binary. Layout and partition offsets per the public XDVDFS
documentation (e.g. https://github.com/XboxDev/extract-xiso,
https://github.com/thrimbor/xbiso).
"""
from __future__ import annotations

import dataclasses
import struct
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

SECTOR_SIZE = 2048
VOLUME_DESCRIPTOR_SECTOR = 32
MAGIC = b"MICROSOFT*XBOX*MEDIA"  # 20 bytes

# Byte offsets, relative to the start of the file, where the game partition
# commonly starts. 0 covers plain/trimmed .iso redistributions (by far the
# most common online); the rest cover raw full-disc dumps of each Xbox disc
# generation.
CANDIDATE_PARTITION_OFFSETS = [0, 0xFB20, 0x20600, 0x02080000, 0x0FD90000, 0x18300000]

ATTR_READONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_NORMAL = 0x80


class XisoError(Exception):
    pass


@dataclasses.dataclass
class DirEntry:
    name: str
    attributes: int
    start_sector: int
    size: int
    table_sector: int  # the sector of the binary-tree table this node lives in

    @property
    def is_dir(self) -> bool:
        return bool(self.attributes & ATTR_DIRECTORY)


class XisoImage:
    """Opens an Xbox .iso/.xiso and walks its XDVDFS directory tree."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._f: Optional[BinaryIO] = None
        self.partition_base = 0
        self.root_sector = 0
        self.root_size = 0

    def __enter__(self) -> "XisoImage":
        self._f = open(self.path, "rb")
        self._locate_partition()
        return self

    def __exit__(self, *exc):
        if self._f:
            self._f.close()
        return False

    def _read(self, abs_offset: int, size: int) -> bytes:
        self._f.seek(abs_offset)
        data = self._f.read(size)
        if len(data) != size:
            raise XisoError(f"short read at {abs_offset:#x} ({len(data)}/{size} bytes)")
        return data

    def _locate_partition(self) -> None:
        file_size = self.path.stat().st_size
        for base in CANDIDATE_PARTITION_OFFSETS:
            vd_off = base + VOLUME_DESCRIPTOR_SECTOR * SECTOR_SIZE
            if vd_off + SECTOR_SIZE > file_size:
                continue
            sector = self._read(vd_off, SECTOR_SIZE)
            if sector[0:20] != MAGIC or sector[0x7EC:0x7EC + 20] != MAGIC:
                continue
            root_sector, root_size = struct.unpack_from("<II", sector, 0x14)
            if root_sector == 0 or root_size == 0:
                continue
            self.partition_base = base
            self.root_sector = root_sector
            self.root_size = root_size
            return
        raise XisoError(
            f"{self.path}: no XDVDFS volume descriptor found (not an Xbox disc image, "
            "or a layout this tool does not recognise yet)"
        )

    def _read_dirent(self, table_sector: int, offset: int) -> Optional[DirEntry]:
        abs_off = self.partition_base + table_sector * SECTOR_SIZE + offset
        head = self._read(abs_off, 16)
        left, right, start_sector, size, attrs = struct.unpack("<HHIIB", head[:13])
        if left == 0xFFFF and right == 0xFFFF:
            return None  # unused / padding slot
        name_len = head[13]
        if name_len == 0 or name_len == 0xFF:
            return None
        name_bytes = self._read(abs_off + 14, name_len)
        name = name_bytes.decode("latin-1")
        return DirEntry(name=name, attributes=attrs, start_sector=start_sector, size=size,
                         table_sector=table_sector), left, right

    def _walk_tree(self, table_sector: int, offset: int) -> Iterator[DirEntry]:
        """In-order walk of the binary search tree table starting at `offset`
        (in bytes, relative to `table_sector`)."""
        result = self._read_dirent(table_sector, offset)
        if result is None:
            return
        entry, left, right = result
        if left:
            yield from self._walk_tree(table_sector, left * 4)
        yield entry
        if right:
            yield from self._walk_tree(table_sector, right * 4)

    def list_dir(self, table_sector: int) -> Iterator[DirEntry]:
        yield from self._walk_tree(table_sector, 0)

    def root(self) -> Iterator[DirEntry]:
        yield from self.list_dir(self.root_sector)

    def find(self, path: str) -> Optional[DirEntry]:
        """Case-insensitive lookup of a '/'-separated path from the disc root."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        table_sector = self.root_sector
        entry = None
        for i, part in enumerate(parts):
            entry = None
            for cand in self.list_dir(table_sector):
                if cand.name.lower() == part.lower():
                    entry = cand
                    break
            if entry is None:
                return None
            if i != len(parts) - 1:
                if not entry.is_dir:
                    return None
                table_sector = entry.start_sector
        return entry

    def read_file(self, entry: DirEntry) -> bytes:
        if entry.is_dir:
            raise XisoError(f"{entry.name}: is a directory")
        return self._read(self.partition_base + entry.start_sector * SECTOR_SIZE, entry.size)

    def extract_file(self, entry: DirEntry, dest: Path, chunk_size: int = 1 << 20) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        remaining = entry.size
        abs_off = self.partition_base + entry.start_sector * SECTOR_SIZE
        with open(dest, "wb") as out:
            self._f.seek(abs_off)
            while remaining > 0:
                n = min(chunk_size, remaining)
                data = self._f.read(n)
                if len(data) != n:
                    raise XisoError(f"short read extracting {entry.name}")
                out.write(data)
                remaining -= n

    def find_default_xbe(self) -> Optional[DirEntry]:
        entry = self.find("default.xbe")
        if entry is not None:
            return entry
        # Some dumps nest everything one folder down; do a shallow search.
        for top in self.root():
            if top.is_dir:
                for sub in self.list_dir(top.start_sector):
                    if sub.name.lower() == "default.xbe":
                        return sub
        return None


def extract_default_xbe(iso_path: Path, dest_xbe: Path) -> Path:
    """Finds and extracts default.xbe from an Xbox .iso. Returns dest_xbe."""
    with XisoImage(iso_path) as iso:
        entry = iso.find_default_xbe()
        if entry is None:
            raise XisoError(f"{iso_path}: no default.xbe found inside the disc image")
        iso.extract_file(entry, dest_xbe)
    return dest_xbe


if __name__ == "__main__":
    import sys

    with XisoImage(Path(sys.argv[1])) as iso:
        print(f"partition base: {iso.partition_base:#x}  root sector: {iso.root_sector}")

        def walk(table_sector: int, prefix: str = ""):
            for e in iso.list_dir(table_sector):
                print(f"{prefix}{e.name}{'/' if e.is_dir else ''}  ({e.size} bytes)")
                if e.is_dir:
                    walk(e.start_sector, prefix + "  ")

        walk(iso.root_sector)
