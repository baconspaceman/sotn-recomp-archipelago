"""Castle shuffler: a Python port of the map/room-layout part of SOTN-Shuffler and BIN-Patcher (both by
Sestren, MIT; see LICENSE in this folder).

generate() follows shuffle.js `multi` with the room and/or stage shuffler on and the solver, reward,
music and hint options off (solver attempt 0), then BIN-Patcher with the change dependencies:
  1. stage links (shuffleStages) and their teleporter table changes;
  2. each stage's rooms shuffled (shuffleRooms) and re-rolled until the stage's checks pass;
  3. the warp rooms attached to the stages they lead to, and the stages arranged on one map;
  4. the map picture and the bought-map reveal mask redrawn;
  5. the rooms' map positions written, and everything that follows from them (room right/bottom,
     the inverted castle, Castle Entrance after the Alchemy Laboratory, boss stages, tile layer
     rectangles, boss teleporters, secret wall reveals, familiar events, warp coordinates);
  6. the data parts of the seed-independent patches the shuffler relies on (normalize-*, a unique
     id for the Power of Wolf relic, and with area colours the map palette).

The result is byte writes per file in the mod's payload keys. Writes into code are left out; they
are listed in CastleLayout.code_sites (seed-dependent) and fixed_code_sites() (in the patches) with
the values they would have had.

The same seed string gives the same castle as the JS shuffler run with --seed=<string>
(tools/castle/check_parity.py).
"""
import random
from typing import Any, Dict, List, Optional, Tuple

from . import rooms as _rooms
from . import stages as _stages
from .data import load
from .logic import stage_is_valid
from .mapdraw import draw, map_pixels, reveals
from .patcher import STAGE_RAM_BASE, RAM_BASE, CodeSite, Patch, Writes, dependencies
from .rooms import Group, Room

__all__ = ["CastleLayout", "generate", "summary", "payload_files", "fixed_code_sites", "seed_name"]

# shuffle.js adds these two Castle Entrance rooms after arranging, out of the way at the top
EXTRA_ROOMS: List[Room] = [
    ("castleEntrance", "forestCutscene", 0, 2),
    ("castleEntrance", "unknownRoom19", 0, 20),
]


class CastleLayout:
    """A generated castle.

    seed:           the seed string the JS would be given (--seed)
    stage_links:    teleporter name -> the teleporter it now leads to (vanilla links when stages aren't shuffled)
    rooms:          (stage, room, row, column) of every arranged room, as shuffle.js's roomPositions
    positions:      stage -> room -> (top, left) map square of every room table entry that gets written,
                    including the inverted castle, Castle Entrance revisited and the boss stages
    writes:         file key -> {offset: byte}
    code_sites:     values BIN-Patcher would write into code, for the mod's own hooks
    patches:        the seed-independent patches whose data is in writes (fixed.json groups)
    stage_attempts: stage -> how many shuffles it took before the stage's checks passed
    changes:        the shuffler's own changes as the JS writes them into its seed file (merge dicts),
                    for comparing with the JS
    """

    def __init__(self, seed: str, shuffle_rooms: bool, shuffle_stages: bool, area_colors: bool, link_labels: bool):
        self.seed = seed
        self.shuffle_rooms = shuffle_rooms
        self.shuffle_stages = shuffle_stages
        self.area_colors = area_colors
        self.link_labels = link_labels
        self.stage_links: Dict[str, str] = _stages.vanilla_stage_links()
        self.rooms: List[Room] = []
        self.positions: Dict[str, Dict[str, Tuple[int, int]]] = {}
        self.writes: Writes = {}
        self.code_sites: List[CodeSite] = []
        self.patches: List[str] = []
        self.stage_attempts: Dict[str, int] = {}
        self.changes: List[Dict[str, Any]] = []

    @property
    def changed(self) -> bool:
        return self.shuffle_rooms or self.shuffle_stages


def seed_name(rng: random.Random) -> str:
    """A seed string from the world's generator. Any string works; this one is just short and plain."""
    return f"AP{rng.getrandbits(48):012X}"


def generate(seed: str, shuffle_rooms: bool = True, shuffle_stages: bool = True, area_colors: bool = True,
             link_labels: bool = True) -> CastleLayout:
    """Shuffles the castle for a seed string. With neither shuffle on, the castle stays vanilla (no writes).

    shuffle_rooms:  shuffle how the rooms of each stage connect
    shuffle_stages: shuffle which stage each teleporter leads to
    area_colors:    colour the map by stage (otherwise the stock map colours, so the map colour option works)
    link_labels:    letter each pair of linked loading rooms on the map (shuffle.js --hinter.stageLinks)
    """
    layout = CastleLayout(seed, shuffle_rooms, shuffle_stages, area_colors, link_labels)
    if not layout.changed:
        return layout
    shuffler = load("shuffler")
    patch = Patch()

    groups: Dict[str, Group] = {}
    if shuffle_stages:
        layout.stage_links = _stages.shuffle_stages(f"{seed}.stageShuffler.0")
        if not shuffle_rooms:
            for stage_name in shuffler["STAGE_NAMES"]:
                groups[stage_name] = _rooms.vanilla_group(stage_name, normalized=False)
        teleporters = {"teleporters": {name: {f"{k}=": v for k, v in fields.items()}
                                       for name, fields in _stages.teleporter_changes(layout.stage_links).items()}}
        patch.merge(teleporters)
        layout.changes.append(teleporters)
    if shuffle_rooms:
        base = f"{seed}.roomShuffler.0"
        for stage_name in shuffler["STAGE_NAMES"]:
            attempt = 0
            while True:
                group = _rooms.shuffle_rooms(f"{base}.{stage_name}.{attempt}", stage_name)
                if stage_is_valid(stage_name, group.rooms):
                    groups[stage_name] = group
                    break
                attempt += 1
            layout.stage_attempts[stage_name] = attempt + 1

    _attach_warp_rooms(groups, layout.stage_links, normalized=shuffle_rooms)
    arranged = _stages.arrange_stages(f"{seed}.stageArranger", list(groups.values()))
    layout.rooms = list(arranged.rooms) + EXTRA_ROOMS

    min_row, min_col = shuffler["MIN_MAP_ROW"], shuffler["MIN_MAP_COL"]
    room_data: Dict[str, int] = {}
    for stage, room, row, col in layout.rooms:
        room_data[f"stages.{stage}.rooms.{room}.top="] = min_row + row
        room_data[f"stages.{stage}.rooms.{room}.left="] = min_col + col
    patch.merge(room_data)
    layout.changes.append(room_data)

    grid = draw(layout.rooms, map_pixels(layout.stage_links, link_labels), area_colors, min_row, min_col)
    picture = {"castleMap.data=": grid}
    reveal_mask = {"castleMapReveals.data=": reveals(grid)}
    patch.merge(picture)
    patch.merge(reveal_mask)
    layout.changes += [picture, reveal_mask]

    for transformation in dependencies():
        patch.evaluate(transformation)
    dynamic, layout.code_sites = patch.writes()
    layout.positions = _room_positions(patch)

    layout.patches = ["powerOfWolf", "normalize"] + (["mapColors"] if area_colors else [])
    fixed = load("fixed")
    writes: Writes = {}
    for name in layout.patches:
        for file, runs in fixed[name]["files"].items():
            target = writes.setdefault(file, {})
            for offset, hex_bytes in runs:
                for i, byte in enumerate(bytes.fromhex(hex_bytes)):
                    target[offset + i] = byte
    for file, bytes_at in dynamic.items():
        target = writes.setdefault(file, {})
        for offset, byte in bytes_at.items():
            if offset in target and target[offset] != byte:
                raise ValueError(f"a fixed patch and the layout both write {file} 0x{offset:X}")
            target[offset] = byte
    layout.writes = writes
    return layout


def _cap(name: str) -> str:
    return name[:1].upper() + name[1:]


def _attach_warp_rooms(groups: Dict[str, Group], stage_links: Dict[str, str], normalized: bool) -> None:
    """Each warp room goes next to the loading room it now leads to (centred on it, overlapping)."""
    teleporters = load("shuffler")["TELEPORTERS"]
    warp_groups = _rooms.node_groups(normalized)["warpRooms"]
    for source, target in stage_links.items():
        if not source.startswith("fromWarpRoomsTo"):
            continue
        stage_name = teleporters[target]["sourceStage"]
        room_name = "loadingRoomTo" + _cap(teleporters[target]["targetStage"])
        warp = warp_groups["warpRoomTo" + source.split("WarpRoomsTo")[1]]
        matches = [r for r in groups[stage_name].rooms if r[0] == stage_name and r[1] == room_name]
        if not matches:
            raise ValueError(f"Room not found for stage '{stage_name}' and room '{room_name}'")
        for _, _, row, col in matches:
            combined = _rooms.combine(groups[stage_name], warp, row, col - 1, allow_overlaps=True)
            if combined is None:
                raise ValueError(f"the {source} warp room does not fit next to {stage_name}.{room_name}")
            groups[stage_name] = combined


def _room_positions(patch: Patch) -> Dict[str, Dict[str, Tuple[int, int]]]:
    result: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for table_path, table in patch.arrays.items():
        if not (table_path.startswith("stages.") and table_path.endswith(".rooms")):
            continue
        stage = table_path[len("stages."):-len(".rooms")]
        for room, index in table["aliases"].items():
            top = patch.data.get((table_path, index, "top"))
            left = patch.data.get((table_path, index, "left"))
            if top is not None and left is not None:
                result.setdefault(stage, {})[room] = (top, left)
    return result


def summary(layout: CastleLayout) -> Dict[str, Any]:
    """For slot_data and the spoiler log: the settings, stage links, every room's map square
    ([top, left], in map squares) and the seed-dependent code sites."""
    return {
        "seed": layout.seed,
        "shuffle_rooms": layout.shuffle_rooms,
        "shuffle_stages": layout.shuffle_stages,
        "area_colors": layout.area_colors,
        "link_labels": layout.link_labels,
        "stage_links": dict(layout.stage_links),
        "rooms": {stage: {room: [top, left] for room, (top, left) in rooms.items()}
                  for stage, rooms in layout.positions.items()},
        "code_sites": [site.as_dict() for site in layout.code_sites],
    }


def fixed_code_sites(layout: CastleLayout) -> List[Dict[str, Any]]:
    """The code changes inside the patches whose data the layout uses (not written; for the mod's hooks):
    file, RAM address, the function, BIN-Patcher's node name and the bytes the patch puts there."""
    result = []
    fixed = load("fixed")
    for name in layout.patches:
        for site in fixed[name]["codeSites"]:
            offset = site["bytes"][0][0]
            result.append({
                "patch": name,
                "file": site["file"],
                "offset": offset,
                "ram": RAM_BASE.get(site["file"], STAGE_RAM_BASE) + offset,
                "function": site["function"],
                "path": site["path"],
                "bytes": bytes(b for _, b in site["bytes"]).hex(),
            })
    return result


def payload_files(layout: CastleLayout) -> Dict[str, List[list]]:
    """The writes in Recomp.py's payload format: {"KEY": [[offset, "hex bytes"], ...]} with runs merged."""
    files: Dict[str, List[list]] = {}
    for key, bytes_at in sorted(layout.writes.items()):
        runs: List[list] = []
        last: Optional[int] = None
        for offset in sorted(bytes_at):
            if last is not None and offset == last + 1:
                runs[-1][1] += f"{bytes_at[offset]:02x}"
            else:
                runs.append([offset, f"{bytes_at[offset]:02x}"])
            last = offset
        files[key] = runs
    return files
