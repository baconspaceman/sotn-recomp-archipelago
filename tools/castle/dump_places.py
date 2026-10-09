"""Where every AP location is in the castle, room by room, for the shuffled-castle logic
(apworld/sotn_recomp/castle/aplogic.py). Writes apworld/sotn_recomp/data/castle/places.json.

usage: python -I tools/castle/dump_places.py [--disc TRACK1.BIN] [--generated DIR] [--out FILE]

Read from the disc (stage files, as tools/map_positions.py does) and from SymphonyRecomp's generated code
(ref/SymphonyRecomp/generated, for function names and the code that sets up enemies):
  - item and relic locations: the room (by its BIN-Patcher alias) and position of the location's entity;
  - boss drops: the boss's room (the boss stage's room, mapped to the stage room it stands for);
  - enemysanity: every room an enemy kind appears in. An enemy is an entity whose update function
    initialises it with the enemy's definition (InitializeEntity(EInit), EInit.enemyId = its index in
    DRA's enemy table), placed in the room or spawned by one placed there (a constant entity id passed
    to CreateEntity*, or a constant update function given to an entity it allocates). The Medusa head
    spawners, whose params choose the heads' colours, and a few spawns the scan can't see are listed;
  - the few spots without either (the Librarian, Maria): the room of their stage entity.
Rooms are named per stage file: castleEntrance is the Castle Entrance at the start of the game (NO3),
castleEntranceRevisited the one every return to it loads (NP3). Locations in the inverted castle are
only flagged as such (the logic doesn't model its layout).

Only positions and room names go in the output, no game data.
"""
import argparse
import json
import os
import re
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import map_positions  # noqa: E402

DEFAULT_DISC = os.path.join(ROOT, "ref", "SymphonyRecomp", "disc",
                            "Castlevania - Symphony of the Night (USA) (Track 1).bin")
DEFAULT_GENERATED = os.path.join(ROOT, "ref", "SymphonyRecomp", "generated")
DEFAULT_OUT = os.path.join(ROOT, "apworld", "sotn_recomp", "data", "castle", "places.json")
DATA = os.path.join(ROOT, "apworld", "sotn_recomp", "data", "castle")

SECTOR, SECTOR_DATA, SECTOR_HEADER = 2352, 2048, 24
DRA_LBA = 299
ENEMY_DEFS = 0x8900          # g_EnemyDefs (0x800A8900) in DRA.BIN
ENEMY_DEF_SIZE = 0x28
ENEMY_DEF_HP = 4

# AP location -> (zone key, stage entity update function) for spots with no item entity
FUNCTION_SPOTS = {
    "Long Library - Librarian Shop Item": ("LIB", "EntityLibrarianChair"),
    "Marble Gallery - Item Given by Maria": ("CEN", "EntityMaria"),
}
# Boss drops in the castle: the boss (an enemy kind) whose rooms, in the location's zones, are the drop's rooms
BOSS_ENEMIES = {
    "Alchemy Lab. - Slogra and Gaibon item": "Slogra",
    "Outer Wall - Doppleganger 10 item": "Doppleganger10",
    "Long Library - Lesser Demon item": "Lesser demon",
    "Clock Tower - Karasuman item": "Karasuman",
    "Royal Chapel - Hippogryph item": "Hippogryph",
    "Colosseum - Minotaur & Werewolf item": "Minotaurus",
    "Olrox's Quarters - Olrox item": "Olrox",
    "Underground Caverns - Scylla item": "Scylla",
    "Abandoned Mine - Cerberus item": "Cerberos",
    "Catacombs - Granfaloon item": "Granfaloon",
}
# The Clock Room in the Center Cube stage (CEN) is reached through Marble Gallery's teleporter room
CEN_ROOM = ("marbleGallery", "triggerTeleporterToCastleCenter")

# Medusa head spawners (sotn-decomp src/st/e_medusa_head.h, medusaHeadSpawnerParams[].yellowChance): out of 16,
# how often a head is yellow, by the spawner's params. Enemy table indices of the two heads (their EInits).
MEDUSA_YELLOW_CHANCE = [0x00, 0x02, 0x08, 0x10, 0x08, 0x08, 0x10, 0x10]
MEDUSA_HEAD_BLUE, MEDUSA_HEAD_YELLOW = 0x12F, 0x130
# Spawns the code scan can't see (the entity id isn't a constant at the call): (file, function) -> entity ids.
# The Long Library's func_us_801BB53C keeps spawning the Lesser Demon (entity 0x1F) until it's beaten.
EXTRA_SPAWNS = {("LIB", "func_us_801BB53C"): [0x1F]}
# Enemy table indices the game uses for a kind besides the one Enemies.py's HP field points at: the
# Corpseweed a Thornweed turns into is initialised with g_EInitCorpseweed (enemyId 159, sotn-decomp
# src/st/chi/e_init.c), Enemies.py's entry being the one before it.
EXTRA_DEFS = {"Corpseweed": {159}}


def load_world():
    """Locations, Enemies and Zones from the AP world, without Archipelago."""
    apworld = os.path.join(ROOT, "apworld")
    sys.path.insert(0, apworld)
    base = types.ModuleType("BaseClasses")
    base.Location = type("Location", (), {})
    sys.modules["BaseClasses"] = base
    pkg = types.ModuleType("sotn_recomp")
    pkg.__path__ = [os.path.join(apworld, "sotn_recomp")]
    sys.modules["sotn_recomp"] = pkg
    import importlib
    return (importlib.import_module("sotn_recomp.Locations"), importlib.import_module("sotn_recomp.Enemies"),
            importlib.import_module("sotn_recomp.data.Zones"))


class Overlay:
    """One overlay's recompiled code: function starts, and per function the enemy definitions it sets up,
    the entity ids it creates and the update functions it gives to entities it allocates."""
    DEF = re.compile(r"public static void (\w+)\(CpuContext c, IMemory m\)")
    ENTRY = re.compile(r"\[0x([0-9A-F]{8})u\] = SoTN\.(\w+),")
    SET = re.compile(r"^\s*c\.(\w+) = (.+?);\s*/\*")
    CALL = re.compile(r"SoTN\.(\w+)\(c, m\);")

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.starts = {}
        for line in lines:
            m = self.ENTRY.search(line)
            if m:
                self.starts[int(m.group(1), 16)] = m.group(2)
        self.bodies = {}
        name, start = None, 0
        for i, line in enumerate(lines):
            m = self.DEF.search(line)
            if m:
                if name:
                    self.bodies[name] = lines[start:i]
                name, start = m.group(1), i
        if name:
            self.bodies[name] = lines[start:]
        self._scan = {}

    # A spawner sets up an entity it allocated: entity->pfnUpdate (+0x28)
    SET_UPDATE = re.compile(r"m\.WriteU32\(\(c\.(\w+) \+ 0x28u\), c\.(\w+)\);")
    COPY = re.compile(r"c\.(\w+) \+ 0u")

    def scan(self, name):
        """(EInit addresses passed to InitializeEntity, entity ids it creates, update functions it gives to
        entities it allocates) in a function's code. Only constants count: anything chosen at run time
        (by params, from a table) is left out, so an enemy is never put somewhere it might not be. An entity
        that turns itself into another (its own pfnUpdate, usually behind a flag) doesn't count either."""
        if name in self._scan:
            return self._scan[name]
        body = self.bodies.get(f"{name}_Impl", self.bodies.get(name, []))
        regs = {}
        self_regs = {"A0"}  # registers holding the entity being updated (a0 on entry)
        einits, created, updates = set(), set(), set()
        for line in body:
            m = self.SET.match(line)
            if m:
                reg, expr = m.group(1), m.group(2)
                copy = self.COPY.fullmatch(expr)
                if copy and copy.group(1) in self_regs:
                    self_regs.add(reg)
                else:
                    self_regs.discard(reg)
                value = self._eval(expr, regs)
                if value is None:
                    regs.pop(reg, None)
                else:
                    regs[reg] = value
                continue
            m = self.SET_UPDATE.search(line)
            if m:
                value = regs.get(m.group(2))
                if value in self.starts and m.group(1) not in self_regs:
                    updates.add(self.starts[value])
                continue
            m = self.CALL.search(line)
            if m:
                callee = m.group(1)
                a0 = regs.get("A0")
                if "InitializeEntity" in callee and a0 is not None:
                    einits.add(a0)
                elif "CreateEntityFrom" in callee and a0 is not None and a0 < 0x400:
                    created.add(a0)
                # A call leaves the argument and return registers unknown
                for reg in ("A0", "A1", "A2", "A3", "V0", "V1"):
                    regs.pop(reg, None)
                    self_regs.discard(reg)
        self._scan[name] = (einits, created, updates)
        return self._scan[name]

    @staticmethod
    def _eval(expr, regs):
        m = re.fullmatch(r"0x([0-9A-F]+)u", expr)
        if m:
            return int(m.group(1), 16)
        m = re.fullmatch(r"0u \| 0x([0-9A-F]+)u", expr)
        if m:
            return int(m.group(1), 16)
        m = re.fullmatch(r"c\.(\w+) \+ 0x([0-9A-F]+)u", expr)
        if m and m.group(1) in regs:
            return (regs[m.group(1)] + int(m.group(2), 16)) & 0xFFFFFFFF
        m = re.fullmatch(r"c\.(\w+) - 0x([0-9A-F]+)u", expr)
        if m and m.group(1) in regs:
            return (regs[m.group(1)] - int(m.group(2), 16)) & 0xFFFFFFFF
        m = re.fullmatch(r"c\.(\w+) \| 0x([0-9A-F]+)u", expr)
        if m and m.group(1) in regs:
            return regs[m.group(1)] | int(m.group(2), 16)
        m = re.fullmatch(r"c\.(\w+) \+ 0u", expr)
        if m and m.group(1) in regs:
            return regs[m.group(1)]
        return None


def entity_table(stage_file, overlay):
    """Entity id -> update function name: the run of function pointers every layout entity id indexes."""
    ids = sorted({e[2] for e in stage_file.layout_entities() if e[2] > 0})
    data = stage_file.data
    words = struct.unpack_from(f"<{len(data) // 4}I", data, 0)
    starts = overlay.starts
    best = None
    for i, word in enumerate(words):
        if word not in starts or any(i + eid - 1 >= len(words) or words[i + eid - 1] not in starts for eid in ids):
            continue
        length = 0
        while i + length < len(words) and words[i + length] in starts:
            length += 1
        if best is None or length > best[1]:
            best = (i, length)
    if best is None:
        raise ValueError("no entity table")
    i, length = best
    return {k + 1: starts[words[i + k]] for k in range(length)}


def dra_offset(disc_offset):
    """A raw disc offset inside DRA.BIN as an offset in the file."""
    rel = disc_offset - DRA_LBA * SECTOR
    sector, within = divmod(rel, SECTOR)
    return sector * SECTOR_DATA + within - SECTOR_HEADER


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--disc", default=DEFAULT_DISC)
    parser.add_argument("--generated", default=DEFAULT_GENERATED)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()

    loc_mod, enemies_mod, zones_mod = load_world()
    facts = json.load(open(os.path.join(DATA, "facts.json")))
    patcher = json.load(open(os.path.join(DATA, "patcher.json")))
    file_stage = {key: stage for stage, key in facts["stageFiles"].items()}
    zone_key = {v: k for k, v in zones_mod.ZONE.items()}

    def room_alias(stage, index):
        aliases = facts["arrays"][f"stages.{stage}.rooms"]["aliases"]
        for alias, i in aliases.items():
            if i == index:
                return alias
        return None

    def primary_room(key, index, x, y):
        """(stage, room, x, y) in the castle's own stages for a room of a stage or boss file, or None."""
        stage = file_stage[key]
        alias = room_alias(stage, index)
        if alias is None:
            return None
        if stage in patcher["BOSS_ROOMS"]:
            info = patcher["BOSS_ROOMS"][stage].get(alias)
            if info is None:
                return None
            source = info["sourceStageName"]
            source_rooms = facts["arrays"][f"stages.{source}.rooms"]["aliases"]
            if alias in source_rooms:  # the boss stage repeats this room of the castle stage
                return source, alias, x, y
            if alias.startswith("triggerTeleporter"):
                return None
            return source, info["sourceRoomName"], x + 256 * info["offsetLeft"], y + 256 * info["offsetTop"]
        if stage == "castleCenter":
            return CEN_ROOM + (128, 128)
        return stage, alias, x, y

    inverted_keys = {k for k in zones_mod.ZONE if k.startswith("R") and k != "RIC"}

    overlays, files, tables = {}, {}, {}

    def stage_file(key):
        if key not in files:
            files[key] = map_positions.StageFile(args.disc, zones_mod.zones[zones_mod.ZONE[key]])
        return files[key]

    def overlay(key):
        if key not in overlays:
            overlays[key] = Overlay(os.path.join(args.generated, f"{key.lower()}.cs"))
        return overlays[key]

    def table(key):
        if key not in tables:
            tables[key] = entity_table(stage_file(key), overlay(key))
        return tables[key]

    def placed(key):
        """[(room index, x, y, params, function name)] of every layout entity in a stage file."""
        sf = stage_file(key)
        rooms = sf.rooms()
        out = []
        for room, at, eid in sf.layout_entities():
            fn = table(key).get(eid)
            if fn is not None:
                out.append((rooms.index(room), sf.u16(at), sf.u16(at + 2), sf.u16(at + 8), fn))
        return out

    def einit_ids(key, einits):
        data = stage_file(key).data
        ids = set()
        for addr in einits:
            off = addr - map_positions.OVERLAY_BASE
            if 0 <= off <= len(data) - 10:
                ids.add(struct.unpack_from("<H", data, off + 8)[0])  # EInit.enemyId
        return ids

    def enemy_ids(key, fn, params):
        """Enemy definitions a placed entity brings into its room: its own, and those of what it spawns."""
        if "MedusaHeadSpawner" in fn:
            # The spawner's params pick a row of medusaHeadSpawnerParams: how often (out of 16) a head is
            # yellow rather than blue
            chance = MEDUSA_YELLOW_CHANCE[params & 0xFF]
            return ({MEDUSA_HEAD_BLUE} if chance < 16 else set()) | ({MEDUSA_HEAD_YELLOW} if chance > 0 else set())
        ov = overlay(key)
        einits, created, updates = ov.scan(fn)
        created = created | set(EXTRA_SPAWNS.get((key, fn), ()))
        functions = set(updates) | {table(key)[eid] for eid in created if eid in table(key)}
        for other in functions:
            einits = einits | ov.scan(other)[0]
        return einit_ids(key, einits)

    def enemy_defs(name):
        """The enemy kind's indices in DRA's enemy table (Enemies.py has its HP fields' disc addresses)."""
        info = enemies_mod.enemy_dict.get(name)
        if info is None:
            return set()
        addresses = info.get("hp_addresses", [info.get("hp_address")])
        defs = {(dra_offset(a) - ENEMY_DEF_HP - ENEMY_DEFS) // ENEMY_DEF_SIZE for a in addresses if a is not None}
        return defs | EXTRA_DEFS.get(name, set())

    result = {}
    problems = []
    # Every enemy kind placed in every castle stage and boss file, by room
    enemy_rooms = {}
    for key in sorted(zones_mod.ZONE):
        z = zones_mod.zones[zones_mod.ZONE[key]]
        if "pos" not in z or key in inverted_keys or key in ("ST0",):
            continue
        if not os.path.exists(os.path.join(args.generated, f"{key.lower()}.cs")):
            continue
        for index, x, y, params, fn in placed(key):
            for enemy in enemy_ids(key, fn, params):
                spot = primary_room(key, index, x, y)
                if spot is not None:
                    enemy_rooms.setdefault(enemy, set()).add((key,) + spot)

    def boss_arenas(keys):
        """{(file key, stage, room, x, y)}: the middle of the main room of each castle boss stage file listed.
        A boss stage file holds one fight in its main room (the others are its teleporter rooms)."""
        out = set()
        for key in keys:
            stage = file_stage.get(key)
            if key in inverted_keys or stage not in patcher["BOSS_ROOMS"]:
                continue
            for index in range(len(stage_file(key).rooms())):
                alias = room_alias(stage, index)
                if alias is not None and not alias.startswith("triggerTeleporter"):
                    spot = primary_room(key, index, 128, 128)
                    if spot is not None:
                        out.add((key,) + spot)
        return out

    def places_of(enemy_name, keys=None):
        """{(file key, stage, room, x, y)} where an enemy kind appears (in the given files only, if given).
        A boss the code scan doesn't find (the Doppelganger, a copy of Alucard made by the boss stage itself)
        is in its boss stage's arena."""
        found = set()
        for d in enemy_defs(enemy_name):
            found |= enemy_rooms.get(d, set())
        if not found:
            listed = [zone_key[z] for z in loc_mod.locations[f"Enemysanity - {enemy_name}"]["zones"]]
            found = boss_arenas(listed)
        return {s for s in found if keys is None or s[0] in keys}

    for name, loc in loc_mod.locations.items():
        keys = [zone_key[z] for z in loc["zones"]]
        inverted = any(k in inverted_keys for k in keys)
        castle_keys = [k for k in keys if k not in inverted_keys]
        entry = {}
        if loc.get("enemy"):
            enemy_name = name.split(" - ", 1)[1]
            if castle_keys and not enemy_defs(enemy_name):
                problems.append(f"{name}: not in Enemies.py")
            found = places_of(enemy_name)
            # Everywhere it is in the castle counts, listed zone or not (the Thornweed is in the Abandoned
            # Mine too). The Succubus lives in her own stage, reached by the false save room's teleport.
            entry = {"enemy": sorted(list(s[1:]) for s in found if s[0] != "DRE")}
            if any(s[0] == "DRE" for s in found):
                entry["succubus"] = True
            if castle_keys and not found:
                problems.append(f"{name}: not found in {castle_keys}")
            unlisted = sorted({s[0] for s in found if s[0] not in keys} - {"NO3", "NP3"})
            if unlisted:
                entry["alsoIn"] = unlisted
        elif loc.get("boss"):
            found = places_of(BOSS_ENEMIES[name], keys) if name in BOSS_ENEMIES else set()
            if not inverted and not found:
                problems.append(f"{name}: boss room not found")
            entry = {"boss": sorted(list(s[1:]) for s in found)}
        elif name in FUNCTION_SPOTS:
            key, fn = FUNCTION_SPOTS[name]
            spots = sorted({primary_room(key, i, x, y) for i, x, y, _, f in placed(key) if f == fn})
            if not spots:
                problems.append(f"{name}: {fn} not placed in {key}")
            entry = {"entity": [list(s) for s in spots]}
        elif "entities" in loc:
            spots = []
            for i, entity in enumerate(loc["entities"]):
                key = keys[i >> 1]
                if key in inverted_keys:
                    continue
                sf = stage_file(key)
                room = sf.room_of_entity(map_positions.OVERLAY_BASE + entity)
                if room is None:
                    continue
                spot = primary_room(key, sf.rooms().index(room), sf.u16(map_positions.OVERLAY_BASE + entity),
                                    sf.u16(map_positions.OVERLAY_BASE + entity + 2))
                if spot is not None and list(spot) not in spots:
                    spots.append(list(spot))
            if castle_keys and not spots:
                problems.append(f"{name}: no room for its entities in {castle_keys}")
            entry = {"entity": spots}
        else:
            problems.append(f"{name}: no way to place it")
        if inverted:
            entry["inverted"] = True
        result[name] = entry

    out = {
        "_source": {"note": "Rooms and positions of the AP locations, from the US disc and SymphonyRecomp's code"},
        "locations": result,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {len(result)} locations to {args.out}")
    for problem in problems:
        print("  problem:", problem)
    for name, entry in result.items():
        if entry.get("alsoIn"):
            print(f"  note: {name} is also in {entry['alsoIn']} (not in its zones)")


if __name__ == "__main__":
    main()
