"""The part of BIN-Patcher (Sestren, MIT) the castle shuffler needs: a patch that starts with every
value unset, the shuffler's changes merged into it, the derived values worked out from them
(sotn-us.js getChangeDependencies and change.js applyEvaluate), and the bytes that come out
(ppf.js parsePatchNode).

Only the tables the castle shuffler touches are modelled (data/castle/facts.json). Paths are
resolved the way getCanonicalPath does: a name that is an alias in a table becomes that element;
anything else is kept as written and can be read back but is never written to the disc.
"""
from typing import Any, Dict, List, Optional, Tuple

from .data import load

# A resolved path: (table path, element index, property path) for a table element,
# (value path, None, "data") for a single value, or ("", None, raw path) for a path that matches no table
Key = Tuple[str, Optional[int], str]

Writes = Dict[str, Dict[int, int]]


# Where files are loaded in RAM (stage and boss files: 0x80180000)
RAM_BASE = {"DRA": 0x800A0000, "RIC": 0x8013C000}
STAGE_RAM_BASE = 0x80180000


class CodeSite:
    """A value BIN-Patcher would write into code (an instruction's immediate). The mod does these with
    its own hooks; they are reported with the value they would have had."""

    def __init__(self, name: str, file: str, offset: int, type_name: str, value: Any):
        self.name = name
        self.file = file
        self.offset = offset
        self.type = type_name
        self.value = value

    @property
    def ram(self) -> int:
        return RAM_BASE.get(self.file, STAGE_RAM_BASE) + self.offset

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "file": self.file, "offset": self.offset, "ram": self.ram,
                "type": self.type, "value": self.value}


class Patch:
    def __init__(self):
        facts = load("facts")
        self.arrays: Dict[str, Dict[str, Any]] = facts["arrays"]
        self.values: Dict[str, Dict[str, Any]] = facts["values"]
        self.addresses: Dict[str, List[Any]] = facts["addresses"]
        self.data: Dict[Key, Any] = {}
        self.code_writes: Dict[str, Tuple[int, str, Any]] = {}

    # ---- paths ----

    def resolve(self, path: str) -> Key:
        tokens = path.split(".")
        for i in range(1, len(tokens)):
            node = ".".join(tokens[:i])
            table = self.arrays.get(node)
            if table is not None:
                index = table["aliases"].get(tokens[i])
                if index is None:
                    return "", None, path
                return node, index, ".".join(tokens[i + 1:])
            if node in self.values:
                return node, None, ".".join(tokens[i:])
        return "", None, path

    def _underscore(self, key: Key) -> Optional[Any]:
        """'_rows' and '_columns' stay unmasked in BIN-Patcher's patch; they come from the extraction."""
        table_path, index, prop = key
        if table_path and index is not None and prop.startswith("_"):
            values = self.arrays[table_path].get(prop)
            if values is None:
                raise KeyError(f"{table_path}.{prop} is not in facts.json")
            return values[index]
        return None

    def get(self, path: str) -> Any:
        """The value at path, or None where the patch has null (unset or no such node)."""
        key = self.resolve(path)
        underscore = self._underscore(key)
        if underscore is not None:
            return underscore
        return self.data.get(key)

    def operand(self, path: str) -> Any:
        """An add/subtract/multiply operand: null counts as 0 (JS arithmetic with null)."""
        key = self.resolve(path)
        underscore = self._underscore(key)
        if underscore is not None:
            return underscore
        if not key[0] and key not in self.data:
            raise KeyError(f"{path}: BIN-Patcher would fail reading this (no such node)")
        value = self.data.get(key)
        return 0 if value is None else value

    def set(self, path: str, value: Any) -> None:
        self.data[self.resolve(path)] = value

    # ---- changes ----

    def merge(self, merge: Dict[str, Any], prefix: str = "") -> None:
        """applyMerge: "a.b.c=" assigns; anything else descends."""
        for path, node in merge.items():
            full = f"{prefix}.{path}" if prefix else path
            if full.endswith("="):
                self.set(full[:-1], node)
            else:
                self.merge(node, full)

    def evaluate(self, actions: List[Dict[str, Any]]) -> None:
        """applyEvaluate: a small stack-free calculator; stops when a property it reads is null."""
        current: Any = None
        for action in actions:
            kind = action["action"]
            by_property = action["type"] == "property"
            if kind == "get":
                if by_property:
                    value = self.get(action["property"])
                    if value is None:
                        return
                    current = value
                else:
                    current = action["constant"]
            elif kind == "set":
                if by_property:
                    self.set(action["property"], current)
                elif action["type"] == "address":
                    self.code_writes[action["name"]] = (action["address"], action["element"]["type"], current)
            elif kind in ("add", "subtract", "multiply"):
                operand = self.operand(action["property"]) if by_property else action["constant"]
                if kind == "add":
                    current = current + operand
                elif kind == "subtract":
                    current = current - operand
                else:
                    current = current * operand

    # ---- output ----

    def writes(self) -> Tuple[Writes, List[CodeSite]]:
        """The bytes parsePatchNode would write for every set value (data only), and the code sites."""
        out: Writes = {}
        code: List[CodeSite] = []

        def put(file: str, offset: int, data: bytes) -> None:
            target = out.setdefault(file, {})
            for i, byte in enumerate(data):
                if offset + i in target and target[offset + i] != byte:
                    raise ValueError(f"two different writes to {file} 0x{offset + i:X}")
                target[offset + i] = byte

        by_element: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for (table_path, index, prop), value in self.data.items():
            if not table_path or value is None:
                continue
            if index is None:
                fact = self.values[table_path]
                if prop != "data":
                    continue
                if fact.get("code"):
                    code.append(CodeSite(table_path, fact["file"], fact["offset"], fact["type"], value))
                    continue
                put(fact["file"], fact["offset"], _encode_value(fact["type"], value))
                continue
            by_element.setdefault((table_path, index), {})[prop] = value

        for (table_path, index), props in by_element.items():
            table = self.arrays[table_path]
            base = table["offset"] + index * table["size"]
            for name, (offset, type_name) in table["props"].items():
                if type_name == "layout-rect":
                    rect = {sub[len(name) + 1:]: v for sub, v in props.items() if sub.startswith(name + ".")}
                    if name in props:
                        rect.update(props[name])
                    if rect:
                        put(table["file"], base + offset, _encode_layout_rect(rect))
                    continue
                if name in props and props[name] is not None:
                    put(table["file"], base + offset, _encode(type_name, props[name]))

        for name, (address, type_name, value) in self.code_writes.items():
            file, offset = self.addresses[str(address)]
            code.append(CodeSite(name, file, offset, type_name, value))
        return out, code


def _encode(type_name: str, value: Any) -> bytes:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{type_name} value {value!r} is not an integer")
    sizes = {"u8": (1, False), "s8": (1, True), "u16": (2, False), "s16": (2, True), "u32": (4, False),
             "s32": (4, True), "room-offset": (2, False), "stage-id": (2, False)}
    if type_name not in sizes:
        raise ValueError(f"unsupported type {type_name}")
    size, signed = sizes[type_name]
    # Buffer.write*() throws for values out of range, so BIN-Patcher would stop here as well
    return value.to_bytes(size, "little", signed=signed)


def _encode_layout_rect(rect: Dict[str, Any]) -> bytes:
    def part(name: str) -> int:
        value = rect.get(name, 0)
        return value if isinstance(value, int) else 0  # undefined << n is 0 in JS

    # 2 * (flags << 23) + ...: the flags byte only when the patch has one; else 3 bytes, keeping it
    value = (2 * (_int32(part("flags") << 23)) + _int32(part("bottom") << 18) + _int32(part("right") << 12)
             + _int32(part("top") << 6) + part("left"))
    size = 4 if "flags" in rect else 3
    return value.to_bytes(4, "little")[:size]


def _int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _encode_value(type_name: str, value: Any) -> bytes:
    if type_name == "indexed-bitmap":
        # 256 rows of 256 colour indices, two per byte, the right-hand pixel in the high nibble
        out = bytearray()
        for row in value[:256]:
            for col in range(128):
                out.append(int(row[2 * col + 1] + row[2 * col], 16))
        return bytes(out)
    if type_name == "binary-string-array":
        out = bytearray([value["left"], value["top"], value["bytesPerRow"], value["rows"]])
        for row in range(value["rows"]):
            text = value["grid"][row]
            for col8 in range(value["bytesPerRow"]):
                byte = 0
                for bit in range(8):
                    i = 8 * col8 + bit
                    # charAt past the end is '' which is not ' ', so it counts as set
                    if i >= len(text) or text[i] != " ":
                        byte |= 1 << bit
                out.append(byte)
        out.append(0xFF)
        return bytes(out)
    return _encode(type_name, value)


def dependencies() -> List[List[Dict[str, Any]]]:
    """getChangeDependencies: every derived-value rule, in the order BIN-Patcher applies them."""
    patcher = load("patcher")
    rooms = patcher["ROOMS"]
    associated = patcher["ASSOCIATED_STAGES"]
    boss_rooms = patcher["BOSS_ROOMS"]
    secondary = set(associated) | set(boss_rooms)
    target: Dict[str, Dict[str, List[Dict[str, Any]]]] = {k: dict(v) for k, v in patcher["TEMPLATE"].items()}
    for name in patcher["ORDERED_DEPENDENCY_NAMES"]:
        target.setdefault(name, {})

    def get(prop):
        return {"action": "get", "type": "property", "property": prop}

    def put(prop):
        return {"action": "set", "type": "property", "property": prop}

    def const(kind, value):
        return {"action": kind, "type": "constant", "constant": value}

    def operand(kind, prop):
        return {"action": kind, "type": "property", "property": prop}

    for stage, stage_rooms in rooms.items():
        group = "secondaryRooms.rightsAndBottoms" if stage in secondary else "primaryRooms.rightsAndBottoms"
        for room in stage_rooms:
            for target_prop, source_prop, dimension in (("right", "left", "_columns"), ("bottom", "top", "_rows")):
                base = f"stages.{stage}.rooms.{room}"
                transformation = [get(f"{base}.{source_prop}"), operand("add", f"{base}.{dimension}"),
                                  const("subtract", 1), put(f"{base}.{target_prop}")]
                target[group][f"{base}.{target_prop}"] = transformation

    for stage, stage_rooms in rooms.items():
        for room, layers in stage_rooms.items():
            for layer in layers:
                for prop in ("left", "top", "right", "bottom"):
                    out = f"stages.{stage}.layers.layerDefinitions.{layer}.layoutRect.{prop}"
                    target["rooms.layerDefinitions"][out] = [get(f"stages.{stage}.rooms.{room}.{prop}"), put(out)]

    for stage, info in associated.items():
        source_stage = info["associatedStageName"]
        if stage not in rooms or source_stage not in rooms:
            continue
        for room in rooms[stage]:
            if room not in rooms[source_stage]:
                continue
            for prop, dimension in (("left", "_columns"), ("top", "_rows")):
                if info["reversed"]:
                    transformation = [const("get", 64),
                                      operand("subtract", f"stages.{source_stage}.rooms.{room}.{prop}"),
                                      operand("subtract", f"stages.{source_stage}.rooms.{room}.{dimension}")]
                else:
                    transformation = [get(f"stages.{source_stage}.rooms.{room}.{prop}")]
                out = f"stages.{stage}.rooms.{room}.{prop}"
                transformation.append(put(out))
                target["secondaryRooms.leftsAndTops"][out] = transformation

    for name, table in (("secretMapTileReveals", patcher["SECRET_MAP_TILE_REVEALS"]),
                        ("bossTeleporters", patcher["BOSS_TELEPORTERS"])):
        for key, info in table.items():
            for source_prop, offset, target_prop in (("top", info["top"], "roomY"), ("left", info["left"], "roomX")):
                transformation = [get(f"stages.{info['stageName']}.rooms.{info['roomName']}.{source_prop}")]
                if offset != 0:
                    transformation.append(const("add", offset))
                out = f"{name}.{key}.{target_prop}"
                transformation.append(put(out))
                target[name][out] = transformation

    for familiar in sorted(patcher["FAMILIAR_OVERLAYS"]):
        for event, info in patcher["FAMILIAR_EVENTS"].items():
            for source_prop, target_prop in (("top", "roomY"), ("left", "roomX")):
                transformation = [get(f"stages.{info['stageName']}.rooms.{info['roomName']}.{source_prop}")]
                if info["inverted"] and source_prop == "left":
                    transformation.append(const("multiply", -1))
                out = f"familiarEvents.{familiar}.{event}.{target_prop}"
                transformation.append(put(out))
                target["familiarEvents"][out] = transformation

    for stage, stage_rooms in boss_rooms.items():
        for room, info in stage_rooms.items():
            for prop, offset in (("top", info["offsetTop"]), ("left", info["offsetLeft"])):
                transformation = [get(f"stages.{info['sourceStageName']}.rooms.{info['sourceRoomName']}.{prop}")]
                if offset != 0:
                    transformation.append(const("add", offset))
                out = f"stages.{stage}.rooms.{room}.{prop}"
                transformation.append(put(out))
                target["bossRooms.leftsAndTops"][out] = transformation

    for repaint in patcher["LIVE_MAP_REPAINTS"]:
        for castle, address in (("firstCastle", repaint["addressA"]), ("reverseCastle", repaint["addressB"])):
            name = f"liveMapRepaints.{castle}.{repaint['specialId']}.{repaint['edge']}"
            target["liveMapRepaints"][name] = [
                get(f"stages.undergroundCaverns.rooms.{repaint['roomName']}.{repaint['edge']}"),
                const("add", repaint["offset"]),
                {"action": "set", "type": "address", "name": name, "address": address,
                 "element": {"structure": "value", "type": "u8"}},
            ]

    result = []
    for name in patcher["ORDERED_DEPENDENCY_NAMES"]:
        result.extend(target[name].values())
    return result
