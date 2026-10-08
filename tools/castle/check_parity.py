"""Holds the Python castle shuffler (apworld/sotn_recomp/castle) against SOTN-Shuffler + BIN-Patcher.

usage:
  python -I tools/castle/check_parity.py --shuffler DIR --disc TRACK1.BIN [--seeds A,B,...]
                                         [--modes both,rooms,stages] [--jobs N] [--keep DIR] [--labels on|off]
                                         [--config ref/SymphonyRecomp/config/sotn.json]

For every seed and mode it runs tools/castle/js_reference.mjs (the JS shuffler and BIN-Patcher) and
castle.generate() with the same seed string, then compares
  - the seed file changes: room positions (keys, values and order), teleporter entries, the map
    picture rows and the reveal mask;
  - the resulting file contents byte for byte (vanilla bytes from the disc where a side doesn't write),
    data only, split into the files the mod receives;
  - the code sites: what BIN-Patcher writes into code against the values castle.generate() reports;
  - the single-colour map (which has no JS counterpart) against the area-colour run of the same seed.
Area colours are on (the JS has no single-colour map); link labels are on unless --labels off.
Code and data are told apart by SymphonyRecomp's function maps (--config). The nose devil familiar table
(TT_006) is compared with BIN-Patcher's missing aliases supplied, as the port does it; a run without
the fix is listed separately.
"""
import argparse
import concurrent.futures
import json
import os
import struct
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apworld", "sotn_recomp"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import castle  # noqa: E402
from castle.patcher import _encode  # noqa: E402
from code_sites import function_at, load_functions  # noqa: E402

SECTOR = 2352
DEFAULT_SEEDS = ["TestSeedA", "TestSeedB", "TestSeedC", "TestSeedD", "RainyMocha62613", "AlphaSeed1",
                 "BetaSeed2", "GammaSeed3", "DeltaSeed4", "EpsilonSeed5", "ZetaSeed6", "ArchipelagoCastle7"]
MODES = {"both": (True, True), "rooms": (True, False), "stages": (False, True)}


class Disc:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.files = []  # (gd_start, gd_end, key, lba, size)
        self._walk(*self._root(), "")
        self.files.sort()
        self._cache = {}

    def sector(self, lba):
        self.f.seek(lba * SECTOR + 24)
        return self.f.read(2048)

    def _root(self):
        pvd = self.sector(16)
        return struct.unpack_from("<I", pvd, 156 + 2)[0], struct.unpack_from("<I", pvd, 156 + 10)[0]

    def _walk(self, lba, size, prefix):
        data = b"".join(self.sector(lba + i) for i in range((size + 2047) // 2048))
        pos = 0
        while pos < len(data):
            length = data[pos]
            if length == 0:
                pos = (pos // 2048 + 1) * 2048
                continue
            rec = data[pos:pos + length]
            pos += length
            extent, extent_size = struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0]
            name = rec[33:33 + rec[32]]
            if name in (b"\x00", b"\x01"):
                continue
            name = name.decode("latin-1").split(";")[0]
            if rec[25] & 2:
                self._walk(extent, extent_size, f"{prefix}/{name}")
            else:
                key = name.split(".")[0].upper()
                self.files.append((extent * 2048, extent * 2048 + extent_size, key, extent, extent_size))

    def where(self, gd):
        for start, end, key, _, _ in self.files:
            if start <= gd < end:
                return key, gd - start
        raise ValueError(f"0x{gd:X} is not in a file")

    def file(self, key):
        if key not in self._cache:
            start, end, _, lba, size = next(f for f in self.files if f[2] == key)
            data = b"".join(self.sector(lba + i) for i in range((size + 2047) // 2048))
            self._cache[key] = data[:size]
        return self._cache[key]


FUNCTIONS = {}  # file key -> (RAM base, [(start, end, name)]), from SymphonyRecomp's function maps


def is_code(key, offset):
    """Code is anything inside a function (writes into code are left to the mod)."""
    return function_at(FUNCTIONS, key, offset)[1] is not None


def run_js(shuffler, disc, seed, mode, keep, labels, nose_devil_fix=True):
    rooms, stages = MODES[mode]
    name = f"js-{seed}-{mode}{'' if labels else '-nolabels'}{'' if nose_devil_fix else '-nofix'}.json"
    out = os.path.join(keep or tempfile.gettempdir(), name)
    if not (keep and os.path.exists(out)):
        subprocess.run(["node", os.path.join(ROOT, "tools", "castle", "js_reference.mjs"), "--shuffler", shuffler,
                        "--disc", disc, "--seed", seed, "--out", out, "--rooms", "on" if rooms else "off",
                        "--stages", "on" if stages else "off", "--labels", "on" if labels else "off",
                        "--nose-devil-fix", "on" if nose_devil_fix else "off"],
                       check=True, stdout=subprocess.DEVNULL)
    with open(out) as f:
        result = json.load(f)
    if not keep:
        os.remove(out)
    return result


def compare(disc, js, layout):
    problems = []
    # -- seed file changes --
    js_changes = {}
    for change in js["changes"]:
        for key, value in change["merge"].items():
            if key == "teleporters":
                js_changes["teleporters"] = value
            elif key.startswith("stages."):
                js_changes.setdefault("rooms", {})[key] = value
            else:
                js_changes[key] = value
    py_changes = {}
    for change in layout.changes:
        for key, value in change.items():
            if key == "teleporters":
                py_changes["teleporters"] = value
            elif key.startswith("stages."):
                py_changes.setdefault("rooms", {})[key] = value
            else:
                py_changes[key] = value
    if list(js_changes.get("rooms", {}).items()) != list(py_changes.get("rooms", {}).items()):
        problems.append("room positions differ")
    if list(js_changes.get("teleporters", {})) != list(py_changes.get("teleporters", {})):
        problems.append("teleporter entries differ")
    else:
        for name, fields in js_changes.get("teleporters", {}).items():
            for field in ("playerX=", "playerY="):
                if fields[field] != py_changes["teleporters"][name][field]:
                    problems.append(f"teleporter {name} {field} differs")
    if js_changes.get("castleMap.data=") != py_changes.get("castleMap.data="):
        problems.append("map picture differs")
    if js_changes.get("castleMapReveals.data=") != py_changes.get("castleMapReveals.data="):
        problems.append("reveal mask differs")

    # -- bytes --
    js_data, js_code = {}, {}
    for gd, value, path in js["writes"]:
        key, offset = disc.where(gd)
        (js_code if is_code(key, offset) else js_data).setdefault(key, {})[offset] = (value, path)
    byte_diffs = []
    for key in sorted(set(js_data) | set(layout.writes)):
        js_bytes = js_data.get(key, {})
        py_bytes = layout.writes.get(key, {})
        vanilla = disc.file(key)
        for offset in sorted(set(js_bytes) | set(py_bytes)):
            want = js_bytes[offset][0] if offset in js_bytes else vanilla[offset]
            have = py_bytes[offset] if offset in py_bytes else vanilla[offset]
            if want != have:
                byte_diffs.append((key, offset, want, have, js_bytes.get(offset, (None, "-"))[1]))
    stats = {
        "js_data_bytes": sum(len(v) for v in js_data.values()),
        "js_code_bytes": sum(len(v) for v in js_code.values()),
        "changed_from_vanilla": sum(1 for key, offsets in js_data.items() for offset, (value, _) in offsets.items()
                                    if disc.file(key)[offset] != value),
    }
    if byte_diffs:
        problems.append(f"{len(byte_diffs)} data bytes differ, first: " + ", ".join(
            f"{k} 0x{o:X} js={w} py={h} ({p})" for k, o, w, h, p in byte_diffs[:8]))

    # -- code sites --
    py_code = {}
    for site in layout.code_sites:
        for i, byte in enumerate(_encode(site.type, site.value)):
            py_code[(site.file, site.offset + i)] = byte
    fixed_code = {}
    for group in ("powerOfWolf", "normalize", "mapColors"):
        for site in castle.load("fixed")[group]["codeSites"]:
            for offset, byte in site["bytes"]:
                fixed_code[(site["file"], offset)] = byte
    for key, offsets in js_code.items():
        for offset, (value, path) in offsets.items():
            have = py_code.get((key, offset), fixed_code.get((key, offset)))
            if have != value:
                problems.append(f"code site {key} 0x{offset:X} ({path}) js={value} py={have}")
    js_code_keys = {(k, o) for k, offsets in js_code.items() for o in offsets}
    for key in list(py_code) + list(fixed_code):
        if key not in js_code_keys:
            problems.append(f"code site {key[0]} 0x{key[1]:X} only in the port")
    for key, bytes_at in layout.writes.items():
        for offset in bytes_at:
            if is_code(key, offset):
                problems.append(f"the port writes {key} 0x{offset:X}, which is inside a function")
    return problems, stats


def compare_single_colour(area, single):
    """The single-colour map (no JS counterpart): everything but the picture and palette is the same as
    with area colours, the picture has the same shape, and it only uses the stock fill, save room, warp
    room and empty indices."""
    problems = []
    for key in set(area.writes) | set(single.writes):
        if key in ("F_MAP", "F_GAME", "F_GAME2"):
            continue
        if area.writes.get(key) != single.writes.get(key):
            problems.append(f"single colour: {key} differs from the area-colour run")
    if "F_GAME" in single.writes or "F_GAME2" in single.writes:
        problems.append("single colour: the map palette is written")
    picture_area = area.writes["F_MAP"]
    picture_single = single.writes["F_MAP"]
    for offset in range(0x8000):
        a, s = picture_area[offset], picture_single[offset]
        for shift in (0, 4):
            pa, ps = (a >> shift) & 15, (s >> shift) & 15
            if (pa == 0) != (ps == 0) or ps not in (0, 1, 4, 5):
                problems.append(f"single colour: F_MAP 0x{offset:X} is {ps:x} where area colours have {pa:x}")
                return problems
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shuffler", required=True)
    parser.add_argument("--disc", required=True)
    parser.add_argument("--seeds", default=",".join(DEFAULT_SEEDS))
    parser.add_argument("--modes", default="both,rooms,stages")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--keep", help="folder to keep (and reuse) the JS outputs in")
    parser.add_argument("--labels", default="on", choices=("on", "off"), help="stage link labels on the map")
    parser.add_argument("--config", default=os.path.join(ROOT, "ref", "SymphonyRecomp", "config", "sotn.json"),
                        help="SymphonyRecomp's config/sotn.json, for its function maps")
    args = parser.parse_args()
    if args.keep:
        os.makedirs(args.keep, exist_ok=True)
    FUNCTIONS.update(load_functions(args.config))
    disc = Disc(args.disc)
    cases = [(seed, mode) for seed in args.seeds.split(",") for mode in args.modes.split(",")]
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        labels = args.labels == "on"
        futures = {case: pool.submit(run_js, args.shuffler, args.disc, case[0], case[1], args.keep, labels)
                   for case in cases}
        failures = 0
        for seed, mode in cases:
            js = futures[(seed, mode)].result()
            rooms, stages = MODES[mode]
            started = time.perf_counter()
            layout = castle.generate(seed, shuffle_rooms=rooms, shuffle_stages=stages, area_colors=True,
                                     link_labels=labels)
            seconds = time.perf_counter() - started
            problems, stats = compare(disc, js, layout)
            single = castle.generate(seed, shuffle_rooms=rooms, shuffle_stages=stages, area_colors=False,
                                     link_labels=labels)
            problems += compare_single_colour(layout, single)
            written = sum(len(v) for v in layout.writes.values())
            status = "OK" if not problems else "MISMATCH"
            print(f"{status:8} {seed:20} {mode:6} js {js['shuffleSeconds']:6.1f}s  py {seconds:5.2f}s  "
                  f"py {written} bytes / js {stats['js_data_bytes']} ({stats['changed_from_vanilla']} changed), "
                  f"code js {stats['js_code_bytes']} bytes / py {len(layout.code_sites)} sites")
            for problem in problems:
                print("         ", problem)
            failures += bool(problems)
    # The JS as published leaves the nose devil's familiar table (TT_006) alone
    js = run_js(args.shuffler, args.disc, cases[0][0], cases[0][1], args.keep, labels, nose_devil_fix=False)
    tt6 = [gd for gd, _, path in js["writes"] if path.startswith("familiarEvents.noseDevil")]
    print(f"without the nose devil alias fix the JS writes {len(tt6)} bytes to TT_006")
    print("all match" if failures == 0 else f"{failures} case(s) differ")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
