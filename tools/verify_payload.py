"""Check the world's slot_data["recomp"] payload against its own writes into the disc image.

Usage (Archipelago venv): ref/archipelago/.venv/Scripts/python.exe tools/verify_payload.py <seed zip> [slot ...]

The seed must be generated with SOTN_RECOMP_DEBUG_TOKENS=1 (tools/make_test_seeds.sh does): the world
then saves each slot's disc writes (<seed>_P<n>_<name>.sotn_tokens) next to it. Every byte written into a
stage/boss file or DRA must be in the payload with the same value (except DRA's time-attack block, lists
the BizHawk client read), and the payload must hold nothing else. The disc's files are found through its
own directory, independently of Recomp.py.

A shuffled castle's writes (slot_data["recomp"]["castle"]: castle.generate() with the seed's settings) are
expected too: the castle's bytes, the disc writes over them (following the layout entries the castle moves,
Recomp.CASTLE_ENTRY_MOVES), and the castle's map palette over those.
"""
import ast
import glob
import importlib.util
import json
import os
import sys
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ref", "archipelago"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gen_location_data as gen  # noqa: E402
from verify_placement import decode_tokens  # noqa: E402

SECTOR, HEADER, DATA = 2352, 24, 0x800
SEED_BLOCK = (0x800DFAEC - 0x800A0000, 0x800DFD44 - 0x800A0000)
SEED_COPY_OFFSET = 0x4298798
DISC = os.path.join(ROOT, "ref", "SymphonyRecomp", "disc", "Castlevania - Symphony of the Night (USA) (Track 1).bin")
# Payload keys for files that aren't AP zones, by disc file name (found through the disc's directory,
# independently of Recomp.py's table). WARNING.TIM is not sent.
FILE_KEYS = {"DRA.BIN": "DRA", "SEL.BIN": "SEL", "MAR.BIN": "MAR", "F_GAME.BIN": "F_GAME",
             "F_GAME2.BIN": "F_GAME2", "RIC.BIN": "RIC", "WARNING.TIM": None}


def disc_files():
    """(name, lba, size) for every file on the disc, from its ISO9660 directory."""
    f = open(DISC, "rb")

    def sector(n):
        f.seek(n * SECTOR)
        return f.read(SECTOR)[HEADER:HEADER + DATA]

    out = []

    def walk(lba, size):
        data = b"".join(sector(lba + i) for i in range(-(-size // DATA)))
        i = 0
        while i < len(data):
            length = data[i]
            if length == 0:
                i = (i // DATA + 1) * DATA
                continue
            name_len = data[i + 32]
            name = data[i + 33:i + 33 + name_len].decode(errors="replace").split(";")[0]
            elba = int.from_bytes(data[i + 2:i + 6], "little")
            esize = int.from_bytes(data[i + 10:i + 14], "little")
            if name not in (chr(0), chr(1)):
                if data[i + 25] & 2:
                    walk(elba, esize)
                else:
                    out.append((name, elba, esize))
            i += length

    root = sector(16)[156:190]
    walk(int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little"))
    return out


def castle_writes(settings):
    """The castle's writes for slot_data's castle settings, from the apworld's castle package (loaded on its
    own, without the world)."""
    if "sotn_recomp" not in sys.modules:
        folder = os.path.join(ROOT, "apworld", "sotn_recomp")
        spec = importlib.util.spec_from_file_location("sotn_recomp", os.path.join(folder, "__init__.py"),
                                                      submodule_search_locations=[folder])
        sys.modules["sotn_recomp"] = importlib.util.module_from_spec(spec)  # not run: just the package
    castle = importlib.import_module("sotn_recomp.castle")
    layout = castle.generate(settings["seed"], shuffle_rooms=settings["shuffle_rooms"],
                             shuffle_stages=settings["shuffle_stages"], area_colors=settings["area_colors"])
    # Recomp.py's table, read from its source (importing it would load Archipelago's worlds).
    source = open(os.path.join(ROOT, "apworld", "sotn_recomp", "Recomp.py"), encoding="utf-8").read()
    moves = next(ast.literal_eval(node.value) for node in ast.parse(source).body
                 if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CASTLE_ENTRY_MOVES")
    return layout.writes, moves


def main():
    zip_path = sys.argv[1]
    import Utils
    with zipfile.ZipFile(zip_path) as z:
        md = Utils.restricted_loads(zlib.decompress(z.read(next(n for n in z.namelist() if n.endswith(".archipelago")))[1:]))
        slots = [int(a) for a in sys.argv[2:]] or sorted(s for s, d in md["slot_data"].items() if "recomp" in d)
        patches = {n: z.read(n) for n in z.namelist() if n.endswith(".sotn_tokens")}
    if not patches:
        sys.exit("no .sotn_tokens files in the seed: generate it with SOTN_RECOMP_DEBUG_TOKENS=1")

    zones_mod, _, _, _ = gen.load_world(os.path.join(ROOT, "apworld", "sotn_recomp"))
    by_key = {v: k for k, v in zones_mod.ZONE.items()}
    spans = [(zn["pos"], zn["pos"] + -(-zn["len"] // DATA) * SECTOR, by_key[num])
             for num, zn in zones_mod.zones.items() if "pos" in zn and "len" in zn]
    named = [(lba * SECTOR, (lba + -(-size // DATA)) * SECTOR, FILE_KEYS[name])
             for name, lba, size in disc_files() if name in FILE_KEYS]

    def to_file(off):
        for s, e, key in spans:
            if s <= off < e:
                sec, w = divmod(off - s, SECTOR)
                return (key, sec * DATA + w) if w < DATA else None
        for s, e, key in named:
            if s <= off < e:
                sec, w = divmod(off - s, SECTOR)
                w -= HEADER
                if key is None or not 0 <= w < DATA:
                    return None
                return (key, sec * DATA + w)
        return ("BIN", off)

    def seed_data(off, where):
        if where[0] == "DRA":
            return SEED_BLOCK[0] <= where[1] < SEED_BLOCK[1]
        if where[0] == "SEL":
            orig = to_file(off - SEED_COPY_OFFSET)
            return orig is not None and orig[0] == "DRA" and SEED_BLOCK[0] <= orig[1] < SEED_BLOCK[1]
        return False

    ok = True
    for slot in slots:
        payload = md["slot_data"][slot].get("recomp")
        name = next(n for n in patches if f"_P{slot}_" in n)
        tokens = decode_tokens(patches[name])
        expected = {}
        castle, moves = castle_writes(payload["castle"]) if "castle" in payload else ({}, [])
        for key, writes in castle.items():
            for off, b in writes.items():
                expected[(key, off)] = b
        moved = [(key, old, new) for key, old, new in moves
                 if any((key, o) in expected for o in range(new, new + 10))]
        for off, data in tokens:
            for i, b in enumerate(data):
                where = to_file(off + i)
                if where is None or seed_data(off + i, where):
                    continue
                for key, old, new in moved:
                    if where[0] == key and old <= where[1] < old + 10:
                        where = (key, where[1] + new - old)
                expected[where] = b
        for key in ("F_GAME", "F_GAME2"):
            for off, b in castle.get(key, {}).items():
                expected[(key, off)] = b
        got = {}
        for key, runs in payload["files"].items():
            for off, hexbytes in runs:
                for i, b in enumerate(bytes.fromhex(hexbytes)):
                    got[(key, off + i)] = b
        wrong = [k for k in expected if got.get(k) != expected[k]]
        extra = [k for k in got if k not in expected]
        size = len(json.dumps(payload))
        per_file = {k: sum(len(h) // 2 for _, h in v) for k, v in payload["files"].items()}
        big = sorted(per_file.items(), key=lambda kv: -kv[1])[:6]
        print(f"slot {slot} ({name}): payload v{payload['version']}, {len(got)} bytes in {len(payload['files'])} files, "
              f"{size / 1024:.1f} KB JSON; wrong {len(wrong)}, extra {len(extra)}; largest {big}")
        ok &= not wrong and not extra
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
