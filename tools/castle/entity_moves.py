"""Room layout entries the castle's fixed patches (data/castle/fixed.json) move or change, for Recomp.py's
CASTLE_ENTRY_MOVES.

usage: py -3.12 tools/castle/entity_moves.py [DISC_BIN]

A room's entity layout is two lists of 10-byte entries (x, y, id, room index, params), sorted by x and
by y, each between FFFE and FFFF markers. For every entry a patch writes into, this prints where the
vanilla entry is after the patch: at another offset (moved; Rom.py's writes into it must follow it) or
nowhere (changed in place: which fields changed, so they can be checked against what Rom.py and the
mod write: x +0, y +2, id +4, params +8).
"""
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DISC = os.path.join(ROOT, "ref", "SymphonyRecomp", "disc", "Castlevania - Symphony of the Night (USA) (Track 1).bin")
FIXED = os.path.join(ROOT, "apworld", "sotn_recomp", "data", "castle", "fixed.json")
SECTOR, HEADER, DATA = 2352, 24, 0x800
ENTRY = 10
FIELDS = ("x", "y", "id", "room index", "params")


def read_files(disc):
    """{path: bytes} reader for the disc's ISO9660 files."""
    f = open(disc, "rb")

    def sector(n):
        f.seek(n * SECTOR)
        return f.read(SECTOR)[HEADER:HEADER + DATA]

    files = {}

    def walk(lba, size, prefix):
        data = b"".join(sector(lba + i) for i in range(-(-size // DATA)))
        i = 0
        while i < len(data):
            length = data[i]
            if length == 0:
                i = (i // DATA + 1) * DATA
                continue
            name = data[i + 33:i + 33 + data[i + 32]].decode(errors="replace").split(";")[0]
            elba = int.from_bytes(data[i + 2:i + 6], "little")
            esize = int.from_bytes(data[i + 10:i + 14], "little")
            if name not in (chr(0), chr(1)):
                path = f"{prefix}/{name}" if prefix else name
                if data[i + 25] & 2:
                    walk(elba, esize, path)
                else:
                    files[path.upper()] = (elba, esize)
            i += length

    root = sector(16)[156:190]
    walk(int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little"), "")
    return lambda path: b"".join(sector(files[path][0] + i) for i in range(-(-files[path][1] // DATA)))[:files[path][1]]


def layout_lists(data):
    """(first entry, end) of every FFFE..FFFF entry list in a stage file."""
    lists = []
    at = data.find(b"\xfe\xff\xfe\xff")
    while at >= 0:
        if at % 2 == 0:
            end = at + ENTRY
            while end + ENTRY <= len(data) and data[end:end + 4] != b"\xff\xff\xff\xff":
                end += ENTRY
            if end + ENTRY <= len(data) and end - at <= 2000:
                lists.append((at + ENTRY, end))
        at = data.find(b"\xfe\xff\xfe\xff", at + 2)
    return lists


def main():
    read = read_files(sys.argv[1] if len(sys.argv) > 1 else DISC)
    fixed = json.load(open(FIXED, encoding="utf-8"))
    for patch_name, patch in fixed.items():
        if not isinstance(patch, dict) or "files" not in patch:
            continue
        for key, writes in sorted(patch["files"].items()):
            if key.startswith("TT_") or key in ("DRA", "F_GAME", "F_GAME2", "F_MAP"):
                continue
            path = f"BOSS/{key}/{key}.BIN" if key.startswith(("BO", "RBO")) else f"ST/{key}/{key}.BIN"
            vanilla = read(path)
            patched = bytearray(vanilla)
            written = set()
            for offset, hex_bytes in writes:
                data = bytes.fromhex(hex_bytes)
                patched[offset:offset + len(data)] = data
                written.update(range(offset, offset + len(data)))
            for first, end in layout_lists(vanilla):
                for at in range(first, end, ENTRY):
                    if not written & set(range(at, at + ENTRY)):
                        continue
                    entry = vanilla[at:at + ENTRY]
                    now = [o for o in range(first, end, ENTRY) if bytes(patched[o:o + ENTRY]) == entry]
                    if now == [at]:
                        continue
                    if now:
                        print(f"{patch_name} {key} 0x{at:X}: moved to 0x{now[0]:X}")
                    else:
                        old = struct.unpack("<HHHHH", entry)
                        new = struct.unpack("<HHHHH", bytes(patched[at:at + ENTRY]))
                        changed = ", ".join(f"{name} {a:#x}->{b:#x}" for name, a, b in zip(FIELDS, old, new) if a != b)
                        print(f"{patch_name} {key} 0x{at:X}: changed in place ({changed})")


if __name__ == "__main__":
    main()
