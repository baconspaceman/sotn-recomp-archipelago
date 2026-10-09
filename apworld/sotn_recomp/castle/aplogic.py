"""Archipelago logic for a shuffled castle, as a graph built from Sestren's room logic (no AP imports).

Nodes are (stage, room, section) places in the castle. Edges come from SOTN-Shuffler's room commands
(constants.js ROOMS_INFO), placed with the seed's room positions and joined across stages by the seed's
stage links, the way the shuffler's own solver builds its whole-castle logic (analyze-logic.js getLogic
with roomPositions and stageLinks). Every requirement becomes a DNF over AP item names and event names:
a list of sets, any one of which is enough; [frozenset()] is always, [] is never.

What Sestren's flags become:
  - progression* flags: the relic or item that gives them (LOGIC.rewards in constants.js);
  - item* counts: the Spike breaker, the Gold and Silver rings;
  - technique* flags: never, but for the ones the options allow (the box puzzle by default: the
    shuffler's solver starts with it, it's no trick);
  - status* flags: events, granted where a command sets them, or set from the start by an option;
  - time: ignored. UNKNOWN_*: never.
A few things are modelled differently from Sestren's solver, where its state can go back (the logic here
only ever gains things):
  - warp rooms: from any warp room, any other one you've been to (the game cycles through the ones you
    have visited, the Castle Entrance's always among them);
  - the Succubus: her fight is there until she's beaten (her flag being off isn't required);
  - the Outer Wall's secret platform: only up while it's lowered, which leaving the room undoes; the
    ways that need it are left out;
  - Castle Entrance: two copies, the one at the start (NO3) and the one every return loads (NP3), each
    with the shortcuts the options open in it;
  - Richter: saved in the Keep's throne room with the Holy glasses (the shuffler's solver never saves him).
Where Sestren's data contradicts itself (a move landing in another section than it's named after, an exit
away from its room's door, a misspelt section, a missing way down), it is corrected: SECTION_FIXES,
POSITION_FIXES, SECTION_TYPOS, EXTRA_COMMANDS.
The inverted castle isn't modelled room by room: it is one place, reached from the first castle's
Keep teleporter once Richter is saved, needing the "inverted kit" (INVERTED_KIT) on top of the usual way
in (Rules.sotn_has_reverse). problem() rejects a castle whose turned-over copy would have parts its
entrance can't reach (inverted_cut_off).
"""
import itertools
from types import SimpleNamespace
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from .data import load
from .logic import _Positions, _update_location
from .rooms import Room

Node = Tuple[str, str, str]           # (stage, room, section)
DNF = List[FrozenSet[str]]            # any one set of items/events is enough
ALWAYS: DNF = [frozenset()]
NEVER: DNF = []

START: Node = ("castleEntrance", "afterDrawbridge", "main")
INVERTED: Node = ("invertedCastle", "", "")
RICHTER_ROOM: Node = ("castleKeep", "keepArea", "throneRoom")
INVERTED_ENTRY: Node = ("castleKeep", "keepArea", "teleporter")
REVISITED = "castleEntranceRevisited"
KILL_DRACULA = "Reverse Center Cube - Kill Dracula"

# What gives each of Sestren's progression flags (constants.js LOGIC.rewards). The Nosedevil card also
# summons a demon in Sestren's logic, but it isn't an item of this world.
FLAG_ITEMS: Dict[str, DNF] = {
    "progressionBatTransformation": [frozenset({"Soul of bat"})],
    "progressionDoubleJump": [frozenset({"Leap stone"})],
    "progressionEcholocation": [frozenset({"Echo of bat"})],
    "progressionGravityJump": [frozenset({"Gravity boots"})],
    "progressionItemMaterialization": [frozenset({"Cube of zoe"})],
    "progressionLongerMistDuration": [frozenset({"Power of mist"})],
    "progressionMistTransformation": [frozenset({"Form of mist"})],
    "progressionSummonDemonFamiliar": [frozenset({"Demon card"})],
    "progressionSummonFerryman": [frozenset({"Merman statue"})],
    "progressionUnlockBlueDoors": [frozenset({"Jewel of open"})],
    "progressionWolfTransformation": [frozenset({"Soul of wolf"})],
    # Moves that only come with a technique flag in the room logic: the technique decides
    "progressionBladeDash": ALWAYS,
    "progressionRisingUppercut": ALWAYS,
}
# item* counts: minimum count -> what has that many
ITEM_COUNTS: Dict[str, Dict[int, DNF]] = {
    "itemSpikeBreaker": {1: [frozenset({"Spike breaker"})]},
    "itemInscribedRing": {1: [frozenset({"Gold ring"}), frozenset({"Silver ring"})],
                          2: [frozenset({"Gold ring", "Silver ring"})]},
}
# Technique flags allowed without any tricks option: the shuffler's solver starts with this one
DEFAULT_TECHNIQUES = frozenset({"techniqueSolveBoxPuzzle"})

# Event item names for Sestren's status flags
EVENTS: Dict[str, str] = {
    "statusBarrierInColosseumOpened": "Colosseum barrier to the Royal Chapel opened",
    "statusBridgeInUndergroundCavernsBroken": "Underground Caverns bridge broken",
    "statusButtonInUndergroundCavernsActivated": "Underground Caverns button pressed",
    "statusCannonActivated": "Alchemy Laboratory cannon fired",
    "statusDemonSwitchActivated": "Abandoned Mine demon switch pressed",
    "statusLightInSpikeMazeActivated": "Catacombs spike maze lit",
    "statusPassageFromCastleEntranceToMarbleGalleryOpened": "Castle Entrance shortcut to the Marble Gallery opened",
    "statusPassageFromCastleEntranceToUndergroundCavernsOpened":
        "Castle Entrance shortcut to the Underground Caverns opened",
    "statusPassageFromCastleEntranceToWarpRoomsOpened": "Castle Entrance shortcut to the warp room opened",
    "statusPressurePlateInMarbleGalleryActivated": "Marble Gallery pressure plate pressed",
    "statusSecretFloorInClockRoomOpened": "Marble Gallery clock room floor opened",
    "statusSecretWallInMermanRoomOpened": "Castle Entrance merman room wall opened",
    "statusStatueInRoyalChapelMoved": "Royal Chapel statue pushed",
    "statusSuccubusDefeated": "Succubus defeated",
    "statusRichterSaved": "Richter saved",
    "statusTrapdoorAfterDrawbridgeOpened": "Castle Entrance trapdoor opened",
    "statusWarpRoomToAbandonedMineUnlocked": "Warp room to the Abandoned Mine visited",
    "statusWarpRoomToCastleKeepUnlocked": "Warp room to the Castle Keep visited",
    "statusWarpRoomToOlroxsQuartersUnlocked": "Warp room to Olrox's Quarters visited",
    "statusWarpRoomToOuterWallUnlocked": "Warp room to the Outer Wall visited",
}
EVENT_NAMES = frozenset(EVENTS.values())
# A requirement on these being off is always met: they only ever go from off to on here
OFF_IS_ALWAYS = {"statusSuccubusDefeated", "statusSecretPlatformInOuterWallActivated"}
# Never counted on: leaving the room resets it
ON_IS_NEVER = {"statusSecretPlatformInOuterWallActivated"}

# sotn_has_flying
FLYING: DNF = [frozenset({"Soul of bat"}), frozenset({"Form of mist", "Power of mist"}),
               frozenset({"Gravity boots", "Leap stone"})]
# The inverted castle is the first castle's layout turned over, so its gates are where the first castle's
# are. Rather than model it room by room, every inverted location needs flight and every item the inverted
# castle's rules in Regions.py and Rules.py use anywhere (sotn_has_reverse; Floating Catacombs, Cave,
# Anti-Chapel, Forbidden Library, Black Marble Gallery, Reverse Outer Wall, Death Wing's Lair, Reverse Entrance,
# Reverse Caverns), less the five Vlad relics: only the goal and the Heart refresh need them, and their own
# rules keep them. And the Cube of zoe: the clock room's statue that opens for the Stopwatch is in the inverted
# one too (RNO0), candles drop nothing without the Cube, and a shuffled castle can put it in the way.
INVERTED_KIT: FrozenSet[str] = frozenset({
    "Holy glasses", "Soul of bat", "Form of mist", "Power of mist", "Gravity boots", "Leap stone",
    "Spike breaker", "Demon card", "Jewel of open", "Soul of wolf", "Holy symbol", "Cube of zoe",
})
# What the first castle needs, from the Keep, that the inverted castle doesn't: the Merman statue (the inverted
# caverns have no ferryman; flight crosses their waterways) and the Gold and Silver rings (the clock room's
# floor; the inverted castle's centre is the Vlad relics' instead, which Rules.py asks of the locations there).
# tools/castle/check_logic.py checks that with these, the kit opens all of the first castle from the Keep.
NOT_IN_INVERTED: FrozenSet[str] = frozenset({"Merman statue", "Gold ring", "Silver ring"})

# Room commands of Sestren's data whose position lands in another section than the one they are named after
# (and the requirements were written for): they go to the section of their name here. Without this, the
# Merman room's hole in the ceiling, the Lesser Demon room's upper level and the Castle Entrance passage to
# the caverns could never be reached, and a drop back to the clock room's floor would come out a room below.
SECTION_FIXES: Dict[Tuple[str, str, str], str] = {
    ("castleEntrance", "mermanRoom", "fromMainToHoleInCeiling"): "holeInCeiling",
    ("castleEntrance", "shortcutToUndergroundCaverns", "toRightSide"): "rightSide",
    ("castleEntrance", "shortcutToUndergroundCaverns", "toMain"): "main",
    ("castleKeep", "dualPlatforms", "toMain"): "main",
    ("longLibrary", "lesserDemonArea", "toUpperLevel"): "upperLevel",
    ("marbleGallery", "clockRoom", "toMain"): "main",
    ("marbleGallery", "threePaths", "toMain"): "main",
    ("royalChapel", "pushingStatueShortcut", "toMain"): "main",
    ("undergroundCaverns", "rightFerrymanRoute", "toRightSide"): "rightSide",
}

# Room exits of Sestren's data that come out at another spot than the door their node group has there (the
# shuffler joins rooms by those doors): here they come out at the door. The pendulum room's lower left exit
# comes out by its upper left one, so the healing mail room behind it could never be reached; the Outer Wall
# elevator shaft's bottom exit comes out a column left of its door, in whatever room is placed there.
POSITION_FIXES: Dict[Tuple[str, str, str], Tuple[int, int]] = {
    ("clockTower", "pendulumRoom", "exitLeftLower"): (-8, 384),
    ("outerWall", "elevatorShaftRoom", "exitBottom"): (304, 2328),  # (the garnet vase room's exitTop is at 304)
}

# A section name misspelt in Sestren's data (the requirements of the Castle Entrance's way up from Death's room
# to the gargoyle room's pit, which therefore never applied)
SECTION_TYPOS: Dict[Tuple[str, str, str], str] = {
    ("castleEntrance", "meetingRoomWithDeath", "highIntheAir"): "highInTheAir",
}

# Room exits missing from Sestren's data. The grand staircase of Olrox's Quarters has the stairwell bottom
# under its middle column (its node group); the stairwell's exitTop climbs up into the staircase with nothing
# needed, but the staircase had no way down, so whatever the shuffler joins to the stairwell bottom's right
# could only be reached from that side.
EXTRA_COMMANDS: Dict[Tuple[str, str], Dict[str, Dict]] = {
    ("olroxsQuarters", "grandStaircase"): {
        "exitBottom": {"outcome": {"positionX": 384, "positionY": 520}, "requirements": [{"section": "main"}]},
    },
}

# Spots of AP locations (data/castle/places.json) outside every section of their room, by room: the section
# they count as in, and what it takes from there. The items below the Clock Tower courtyard's broken bridge
# and the vases up Olrox's Quarters' tall shaft hang above the floor; Flea riders fly about the Keep. Flight
# reaches anything in the open above the floor (the vanilla logic, Rules.py and Regions.py, asks no more of
# them). Other spots outside every section are left out: every location they belong to has other spots.
OUTSIDE_SPOTS: Dict[Tuple[str, str], Tuple[str, DNF]] = {
    ("clockTower", "openCourtyard"): ("main", FLYING),
    ("olroxsQuarters", "tallShaft"): ("main", FLYING),
    ("castleKeep", "keepArea"): ("main", FLYING),
}

STAGE_NAMES = {
    "abandonedMine": "Abandoned Mine", "alchemyLaboratory": "Alchemy Laboratory",
    "castleEntrance": "Castle Entrance", REVISITED: "Castle Entrance Revisited", "castleKeep": "Castle Keep",
    "catacombs": "Catacombs", "clockTower": "Clock Tower", "colosseum": "Colosseum", "longLibrary": "Long Library",
    "marbleGallery": "Marble Gallery", "olroxsQuarters": "Olrox's Quarters", "outerWall": "Outer Wall",
    "royalChapel": "Royal Chapel", "undergroundCaverns": "Underground Caverns", "warpRooms": "Warp Rooms",
}


# ---- DNF helpers ----

def simplify(dnf: Iterable[FrozenSet[str]]) -> DNF:
    """Drops the sets that contain another (having more never hurts)."""
    out: DNF = []
    for s in sorted(set(dnf), key=lambda x: (len(x), sorted(x))):
        if not any(o <= s for o in out):
            out.append(s)
    return out


def dnf_and(a: DNF, b: DNF) -> DNF:
    return simplify([x | y for x in a for y in b])


def dnf_or(a: DNF, b: DNF) -> DNF:
    return simplify(list(a) + list(b))


def satisfied(dnf: DNF, have: Set[str]) -> bool:
    return any(s <= have for s in dnf)


# ---- options ----

class Options:
    """The world options the graph depends on.

    caverns_back_door: 0 closed, 1 open in the Castle Entrance after the first visit, 2 open from the start
    colosseum_back_door, open_shortcuts: as the world options
    techniques: technique* flags allowed (a future tricks option)
    """

    def __init__(self, caverns_back_door: int = 0, colosseum_back_door: bool = False, open_shortcuts: bool = False,
                 techniques: Iterable[str] = DEFAULT_TECHNIQUES):
        self.caverns_back_door = caverns_back_door
        self.colosseum_back_door = colosseum_back_door
        self.open_shortcuts = open_shortcuts
        self.techniques = frozenset(techniques)

    def initially_set(self, stage: str) -> Set[str]:
        """Status flags an option opens from the start, in a stage (the two Castle Entrances differ).
        Rom.py: caverns_back_door patches the Castle Entrance's check of the caverns passage in NP3 (1)
        or in NO3 and NP3 (2); colosseum_back_door the Colosseum barrier's (ARE); open_shortcuts
        (unlocked_patches) opens the Castle Entrance passages to the Underground Caverns, the Marble Gallery
        and the warp room (NO3 and NP3), the Royal Chapel statue (DAI) and the Colosseum barrier (ARE)."""
        flags = set()
        if self.caverns_back_door == 2 or (self.caverns_back_door == 1 and stage == REVISITED):
            flags.add("statusPassageFromCastleEntranceToUndergroundCavernsOpened")
        if self.colosseum_back_door:
            flags.add("statusBarrierInColosseumOpened")
        if self.open_shortcuts:
            flags |= {"statusPassageFromCastleEntranceToUndergroundCavernsOpened",
                      "statusPassageFromCastleEntranceToMarbleGalleryOpened",
                      "statusPassageFromCastleEntranceToWarpRoomsOpened",
                      "statusStatueInRoyalChapelMoved", "statusBarrierInColosseumOpened"}
        return flags


# ---- the graph ----

class Graph:
    """nodes: every place; edges: (from, to) -> DNF; events: name -> [(node, DNF)] (any one grant will do);
    locations: AP location name -> [(node, DNF)] (any one place will do); unplaced: AP locations with no
    place; dropped: room commands left out."""

    def __init__(self):
        self.nodes: Set[Node] = set()
        self.edges: Dict[Tuple[Node, Node], DNF] = {}
        self.events: Dict[str, List[Tuple[Node, DNF]]] = {}
        self.locations: Dict[str, List[Tuple[Node, DNF]]] = {}
        self.start: Node = START
        self.unplaced: List[str] = []
        self.dropped: List[str] = []

    def add_edge(self, a: Node, b: Node, dnf: DNF) -> None:
        self.nodes.add(a)
        self.nodes.add(b)
        if not dnf or a == b:
            return
        key = (a, b)
        self.edges[key] = dnf_or(self.edges[key], dnf) if key in self.edges else simplify(dnf)

    @staticmethod
    def _add_place(places: List[Tuple[Node, DNF]], node: Node, dnf: DNF) -> None:
        for i, (n, d) in enumerate(places):
            if n == node:
                places[i] = (n, dnf_or(d, dnf))
                return
        places.append((node, simplify(dnf)))

    def add_event(self, name: str, node: Node, dnf: DNF) -> None:
        self.nodes.add(node)
        if dnf:
            self._add_place(self.events.setdefault(name, []), node, dnf)

    def add_location(self, name: str, node: Node, dnf: DNF) -> None:
        self.nodes.add(node)
        if dnf:
            self._add_place(self.locations.setdefault(name, []), node, dnf)

    def drop_missing_events(self) -> None:
        """An event nothing grants is never had: drops the requirement sets that need one."""
        while True:
            granted = set(self.events)
            missing = {name for dnf in self._dnfs() for s in dnf for name in s
                       if name in EVENT_NAMES and name not in granted}
            if not missing:
                return

            def keep(dnf: DNF) -> DNF:
                return [s for s in dnf if not (s & missing)]
            self.edges = {k: keep(d) for k, d in self.edges.items() if keep(d)}
            self.events = {n: [(node, keep(d)) for node, d in g if keep(d)] for n, g in self.events.items()}
            self.events = {n: g for n, g in self.events.items() if g}
            self.locations = {n: [(node, keep(d)) for node, d in p if keep(d)] for n, p in self.locations.items()}
            self.locations = {n: p for n, p in self.locations.items() if p}

    def _dnfs(self):
        yield from self.edges.values()
        for grants in itertools.chain(self.events.values(), self.locations.values()):
            for _, dnf in grants:
                yield dnf


def node_name(node: Node) -> str:
    """A readable name, as "Alchemy Laboratory - Red Skeleton Lift Room (main)"."""
    stage, room, section = node
    if node == INVERTED:
        return "Inverted Castle"
    return f"{STAGE_NAMES.get(stage, stage)} - {_words(room)} ({_words(section).lower()})"


def _words(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if i and ((ch.isupper() and not name[i - 1].isupper()) or (ch.isdigit() and not name[i - 1].isdigit())):
            out.append(" ")
        out.append(ch)
    text = "".join(out)
    return text[:1].upper() + text[1:]


class _Builder:
    def __init__(self, layout, options: Options):
        self.options = options
        self.logic = load("logic")
        self.shuffler = load("shuffler")
        self.rooms_info = self.logic["ROOMS_INFO"]
        self.rooms: Sequence[Room] = layout.rooms
        self.placed = {(s, r) for s, r, _, _ in self.rooms}
        self.positions = _Positions(self.rooms)
        self.stage_links: Dict[str, str] = layout.stage_links
        self.graph = Graph()

    # -- requirements --

    def status(self, flag: str, value: bool, stage: str) -> DNF:
        if not value:
            if flag in OFF_IS_ALWAYS:
                return ALWAYS
            raise ValueError(f"a requirement on {flag} being off isn't modelled")
        if flag in ON_IS_NEVER:
            return NEVER
        if flag in self.options.initially_set(stage):
            return ALWAYS
        if flag not in EVENTS:
            raise ValueError(f"unknown status flag {flag}")
        return [frozenset({EVENTS[flag]})]

    def requirement(self, req: Dict, stage: str) -> DNF:
        dnf = ALWAYS
        for key, value in req.items():
            if key in ("section", "stage", "room", "costs") or key.startswith("location"):
                continue  # where (the node), time (ignored), a location's own flag
            if key.startswith("progression"):
                dnf = dnf_and(dnf, FLAG_ITEMS[key])
            elif key.startswith("technique"):
                if key not in self.options.techniques:
                    return NEVER
            elif key.startswith("status"):
                dnf = dnf_and(dnf, self.status(key, value, stage))
            elif key.startswith("item"):
                dnf = dnf_and(dnf, ITEM_COUNTS[key][value["minimum"]])
            elif key.startswith("UNKNOWN"):
                return NEVER
            else:
                raise ValueError(f"unknown requirement {key}")
            if not dnf:
                return NEVER
        return dnf

    # -- places --

    def resolve(self, stage: str, room: str, x: int, y: int, stale: bool = False,
                section: str = "NONE") -> Optional[Node]:
        """The (stage, room, section) a position ends up in (updateLocation), or None (no section there)."""
        location = {"stage": stage, "room": room, "section": section, "positionX": x, "positionY": y,
                    "staleLocation": stale}
        _update_location(location, self.positions, False)
        if location["section"] == "NONE":
            return None
        return location["stage"], location["room"], location["section"]

    def sections(self, stage: str, room: str) -> List[str]:
        info = self.rooms_info.get(stage, {}).get(room)
        return sorted({r["outcome"]["section"] for r in info["regions"]}) if info else []

    # -- building --

    def build(self) -> Graph:
        for stage, room in sorted(self.placed):
            info = self.rooms_info.get(stage, {}).get(room)
            if info is None:
                continue
            for copy in ([stage, REVISITED] if stage == "castleEntrance" else [stage]):
                for section in self.sections(stage, room):
                    self.graph.nodes.add((copy, room, section))
                self.room_commands(copy, stage, room, info)
                for region in info["regions"]:
                    for key, value in region["outcome"].items():
                        if key.startswith("status") and value is True:
                            self.graph.add_event(EVENTS[key], (copy, room, region["outcome"]["section"]), ALWAYS)
        self.stage_link_edges()
        self.warp_edges()
        self.inverted_entry()
        return self.graph

    def room_commands(self, copy: str, stage: str, room: str, info: Dict) -> None:
        commands = dict(info.get("commands", {}), **EXTRA_COMMANDS.get((stage, room), {}))
        for name, command in commands.items():
            if stage == "warpRooms" and name.startswith("teleportTo"):
                continue  # warp_edges
            outcome = command["outcome"]
            if (stage, room, name) in POSITION_FIXES:
                x, y = POSITION_FIXES[(stage, room, name)]
                outcome = dict(outcome, positionX=x, positionY=y)
            moves = any(k in outcome for k in ("stage", "room", "section", "positionX", "positionY"))
            for req in command["requirements"]:
                section = SECTION_TYPOS.get((stage, room, req["section"]), req["section"])
                source = (copy, room, section)
                dnf = self.requirement(req, copy)
                if not dnf:
                    continue
                if (stage, room, name) in SECTION_FIXES:
                    self.graph.add_edge(source, (copy, room, SECTION_FIXES[(stage, room, name)]), dnf)
                elif moves:
                    target = self.resolve(outcome.get("stage", stage), outcome.get("room", room),
                                          outcome.get("positionX", 0), outcome.get("positionY", 0),
                                          outcome.get("staleLocation", False), outcome.get("section", section))
                    if target is None:
                        self.graph.dropped.append(f"{node_name(source)}: {name} leads nowhere")
                        continue
                    if target[0] == "castleEntrance" and copy == REVISITED:
                        target = (REVISITED,) + target[1:]
                    self.graph.add_edge(source, target, dnf)
                for key, value in outcome.items():
                    if value is True and key in EVENTS:  # (meeting Death and the secret platform: not needed)
                        self.graph.add_event(EVENTS[key], source, dnf)

    def stage_link_edges(self) -> None:
        """A loading room's teleporter leads to where the teleporter it's linked to puts you."""
        teleporters = self.shuffler["TELEPORTERS"]
        for source, target in self.stage_links.items():
            info = teleporters[target]
            landing = self.resolve(info["sourceStage"], info["room"], info["positionX"], info["positionY"])
            if landing is None:
                raise ValueError(f"{target}: lands outside any section")
            if landing[0] == "castleEntrance":
                landing = (REVISITED,) + landing[1:]  # any return to the Castle Entrance loads NP3
            from_stage = teleporters[source]["sourceStage"]
            to_stage = teleporters[source]["targetStage"]
            trigger = "triggerTeleporterTo" + to_stage[:1].upper() + to_stage[1:]
            for copy in ([from_stage, REVISITED] if from_stage == "castleEntrance" else [from_stage]):
                for section in self.sections(from_stage, trigger):
                    self.graph.add_edge((copy, trigger, section), landing, ALWAYS)

    def warp_edges(self) -> None:
        """A warp takes you to the next warp room you've been in, in a cycle: in turn, to any of them."""
        rooms = sorted(r for s, r in self.placed if s == "warpRooms" and r.startswith("warpRoomTo"))
        for a, b in itertools.permutations(rooms, 2):
            flag = f"statusWarpRoomTo{b[len('warpRoomTo'):]}Unlocked"
            dnf = [frozenset({EVENTS[flag]})] if flag in EVENTS else ALWAYS  # the Castle Entrance's: always
            self.graph.add_edge(("warpRooms", a, "main"), ("warpRooms", b, "main"), dnf)

    def inverted_entry(self) -> None:
        richter = EVENTS["statusRichterSaved"]
        # Richter is saved in the throne room with the Holy glasses; without them the game ends there
        self.graph.add_event(richter, RICHTER_ROOM, [frozenset({"Holy glasses"})])
        reverse = dnf_and([frozenset({"Holy glasses"})], FLYING)  # sotn_has_reverse
        self.graph.add_edge(INVERTED_ENTRY, INVERTED, dnf_and(reverse, [INVERTED_KIT | {richter}]))

    # -- AP locations --

    def location_extras(self) -> Dict[Tuple[str, str], DNF]:
        """Requirements of Sestren's item locations beyond being in the room (the Gold ring is only there
        once the Succubus is beaten), for every location in that room."""
        extras = {}
        for info in self.logic["LOCATIONS"].values():
            for req in info["requirements"]:
                dnf = self.requirement({k: v for k, v in req.items() if not k.startswith("location")}, req["stage"])
                if dnf != ALWAYS:
                    key = (req["stage"], req["room"])
                    extras[key] = dnf_or(extras.get(key, NEVER), dnf)
        return extras

    def place_locations(self, names: Iterable[str]) -> None:
        """Puts the AP locations on the graph (where data/castle/places.json says they are)."""
        places = load("places")["locations"]
        extras = self.location_extras()
        succubus = EVENTS["statusSuccubusDefeated"]
        for name in names:
            if name == KILL_DRACULA:
                self.graph.add_location(name, INVERTED, ALWAYS)
                continue
            info = places.get(name, {})
            spots = info.get("entity") or info.get("boss") or info.get("enemy") or []
            for stage, room, x, y in spots:
                base = "castleEntrance" if stage == REVISITED else stage
                if (base, room) not in self.placed:
                    continue
                size = self.rooms_info[base][room]["roomInfo"]
                x = min(max(x, 0), size["width"] - 1)  # (the Scylla wyrm stands on the room's edge)
                y = min(max(y, 0), size["height"] - 1)
                node = self.resolve(base, room, x, y)
                dnf = extras.get((base, room), ALWAYS)
                if node is None:
                    if (base, room) not in OUTSIDE_SPOTS:
                        continue
                    section, needs = OUTSIDE_SPOTS[(base, room)]
                    node, dnf = (base, room, section), dnf_and(dnf, needs)
                if stage == REVISITED:
                    node = (REVISITED,) + node[1:]
                self.graph.add_location(name, node, dnf)
            if info.get("succubus"):
                # In the Succubus's dream: she is fought there, the fight is where she's beaten
                for node, _ in self.graph.events.get(succubus, []):
                    self.graph.add_location(name, node, [frozenset({succubus})])
            if info.get("inverted"):
                self.graph.add_location(name, INVERTED, ALWAYS)
            if name not in self.graph.locations:
                self.graph.unplaced.append(name)


def build_graph(layout, options: Options, locations: Iterable[str] = ()) -> Graph:
    """The castle's graph with the given AP locations placed on it. layout: a CastleLayout (its rooms and
    stage_links)."""
    builder = _Builder(layout, options)
    builder.build()
    builder.place_locations(locations)
    builder.graph.drop_missing_events()
    return builder.graph


def vanilla_layout():
    """The vanilla castle as a layout (rooms and stage links), for comparing with a shuffled one."""
    from . import EXTRA_ROOMS, _attach_warp_rooms
    from . import rooms as _rooms, stages as _stages
    links = _stages.vanilla_stage_links()
    groups = {stage: _rooms.vanilla_group(stage, normalized=False) for stage in load("shuffler")["STAGE_NAMES"]}
    _attach_warp_rooms(groups, links, normalized=False)
    arranged = _stages.arrange_stages("vanilla.stageArranger", list(groups.values()))
    return SimpleNamespace(rooms=list(arranged.rooms) + EXTRA_ROOMS, stage_links=links)


# ---- reachability ----

def reachable(graph: Graph, items: Iterable[str]) -> Set[str]:
    """The AP locations reachable with the given items (and the events they lead to)."""
    have = set(items)
    reached = reach(graph, have)
    return {name for name, places in graph.locations.items()
            if any(node in reached and satisfied(dnf, have) for node, dnf in places)}


def reach(graph: Graph, have: Set[str], start: Optional[Node] = None) -> Set[Node]:
    """Nodes reachable from the start; adds the events collected on the way to have."""
    out_edges: Dict[Node, List[Tuple[Node, DNF]]] = {}
    for (a, b), dnf in graph.edges.items():
        out_edges.setdefault(a, []).append((b, dnf))
    reached = {start or graph.start}
    while True:
        stack = list(reached)
        while stack:
            node = stack.pop()
            for target, dnf in out_edges.get(node, ()):
                if target not in reached and satisfied(dnf, have):
                    reached.add(target)
                    stack.append(target)
        new_events = {name for name, grants in graph.events.items() if name not in have and
                      any(node in reached and satisfied(dnf, have) for node, dnf in grants)}
        if not new_events:
            return reached
        have |= new_events


def prune(graph: Graph, all_items: Iterable[str]) -> None:
    """Keeps only what can ever be had: the places reachable with every item and the events had there (a
    requirement on another event can never be met, so it goes)."""
    have = set(all_items)
    reached = reach(graph, have)
    graph.events = {name: [(node, dnf) for node, dnf in grants if node in reached]
                    for name, grants in graph.events.items() if name in have}
    graph.drop_missing_events()
    graph.nodes = reached
    graph.edges = {(a, b): dnf for (a, b), dnf in graph.edges.items() if a in reached and b in reached}
    graph.locations = {name: [(node, dnf) for node, dnf in places if node in reached]
                       for name, places in graph.locations.items()}


def inverted_cut_off(graph: Graph, all_items: Iterable[str]) -> List[Node]:
    """Places of the first castle that can be reached from the start but not from the Keep where the castle
    turns over (with every item, the switches and warp rooms to be found again on the way). The inverted
    castle is the first one turned over: the same rooms side by side and the same stage links, so with
    flight its one-way ways (switches, shortcuts, warp rooms) are where this finds them, and a place cut off
    here is cut off there."""
    have = set(all_items)
    everywhere = reach(graph, set(have))
    from_keep = reach(graph, have | {EVENTS["statusRichterSaved"]}, INVERTED_ENTRY)
    # (the start's Castle Entrance, NO3, is never returned to: the inverted castle's turns over NP3)
    return sorted(n for n in everywhere - from_keep if n[0] != "castleEntrance" and n != INVERTED)


# A castle is rerolled when fewer locations than this, that can hold progression, are reachable with no items.
# Archipelago's fill copes with one (tried on item pools down to key items only, where a shuffled castle
# often has just one or two); asking more, a share of the vanilla castle's, would reroll most castles with
# the smaller pools. The vanilla castle has, in this logic (stricter than Regions.py's), 5 with the key items
# or guarded pool, 11 with equipment, 30 with everything, 74 with boss drops and enemysanity too.
MIN_FREE_LOCATIONS = 1


def problem(graph: Graph, all_items: Iterable[str], min_free: int = MIN_FREE_LOCATIONS,
            counts_as_free: Optional[Callable[[str], bool]] = None) -> Optional[str]:
    """Why a castle can't be used, or None: a location unreachable even with every progression item, part of
    the inverted castle out of reach of its entrance, or fewer than min_free locations reachable with no
    items (of those counts_as_free accepts, if given: the world's rules and item rules)."""
    all_items = set(all_items)
    if graph.unplaced:
        return f"{len(graph.unplaced)} locations have no place, as {graph.unplaced[0]}"
    missing = sorted(set(graph.locations) - reachable(graph, all_items))
    if missing:
        return f"{len(missing)} locations unreachable with every item, as {missing[0]}"
    cut = inverted_cut_off(graph, all_items)
    if cut:
        return f"the inverted castle is cut off from its entrance at {len(cut)} places, as {node_name(cut[0])}"
    free = [name for name in reachable(graph, ()) if counts_as_free is None or counts_as_free(name)]
    if len(free) < min_free:
        return f"{len(free)} locations reachable with no items, under {min_free}"
    return None
