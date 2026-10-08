"""The per-stage checks SOTN-Shuffler runs on a shuffled stage (constants.js VALIDATIONS, checked with
analyze-logic.js getLogic and validate).

getLogic turns every room command into a transition whose destination (room and section) is worked
out from the room positions, exactly as the JS does. validate() in the JS is a depth-first search that
keys states by stage, room, section and a hash of the remaining flags, revisiting a state when it is
reached with more time left. Since time only goes down and every requirement on it is a minimum,
that search ends up with the best time for every state; here a best-first search (most time first)
reaches the same states and so gives the same answer. The JS keys flags by a 32-bit hash; two
different flag sets colliding within one search is not reproduced (it is about a 1 in 4 billion chance).
"""
import heapq
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .data import load
from .rooms import Room

_LOCATION_KEYS = ("stage", "room", "section", "positionX", "positionY", "staleLocation")
_KEEP_ON_SIMPLIFY = {"stage", "room", "section", "time", "positionX", "positionY", "debugEnableElsewhere"}


def _is_valid_requirement(state: Dict[str, Any], requirement: Dict[str, Any]) -> bool:
    for key, wanted in requirement.items():
        if isinstance(wanted, bool):
            have = state.get(key, False)
            if not (isinstance(have, bool) and have == wanted):
                return False
        elif isinstance(wanted, str):
            if state.get(key, "NONE") != wanted:
                return False
        elif isinstance(wanted, dict):
            have = state.get(key, 0)
            if "minimum" in wanted and have < wanted["minimum"]:
                return False
            if "maximum" in wanted and have > wanted["maximum"]:
                return False
    return True


def _simplify(state: Dict[str, Any]) -> None:
    for key, value in list(state.items()):
        if key in _KEEP_ON_SIMPLIFY:
            continue
        if isinstance(value, bool):
            if value is False:
                del state[key]
        elif isinstance(value, (int, float)):
            if value == 0:
                del state[key]
        elif isinstance(value, str):
            if value == "NONE":
                del state[key]


def _apply_outcome(state: Dict[str, Any], outcome: Dict[str, Any], simplify: bool = True) -> None:
    """updateStateWithOutcome"""
    for key, value in outcome.items():
        if isinstance(value, dict):
            operation = value.get("operation")
            if operation == "replace":
                state[key] = value["value"]
            elif operation == "add":
                state[key] = state.get(key, 0) + value["value"]
        else:
            state[key] = value
    if simplify:
        _simplify(state)


class _Positions:
    """The room positions getLogic is given, and the room rectangles getRoomDimensions makes of them."""

    def __init__(self, rooms: Sequence[Room]):
        self.rooms = rooms
        self._first: Dict[Tuple[str, str], Tuple[int, int]] = {}
        for stage, room, row, col in rooms:
            self._first.setdefault((stage, room), (row, col))
        self._rects: Optional[Dict[Tuple[str, str], Tuple[int, int, int, int]]] = None

    def first(self, stage: str, room: str) -> Optional[Tuple[int, int]]:
        return self._first.get((stage, room))

    def rect(self, stage: str, room: str) -> Optional[Tuple[int, int, int, int]]:
        if self._rects is None:
            rooms_info = load("logic")["ROOMS_INFO"]
            rects = {}
            for s, r, row, col in self.rooms:  # later entries win, as in the JS
                info = rooms_info.get(s, {}).get(r)
                if info is not None:
                    left, top = 256 * col, 256 * row
                    rects[(s, r)] = (left, top, left + info["roomInfo"]["width"], top + info["roomInfo"]["height"])
            self._rects = rects
        return self._rects.get((stage, room))


def _update_location(location: Dict[str, Any], positions: _Positions, simplify: bool) -> None:
    """updateLocation: moves a position that left its room into the room that holds it, then finds
    the section of the room the position is in."""
    logic = load("logic")
    rooms_info = logic["ROOMS_INFO"]
    stage = location["stage"]
    info = rooms_info[stage][location["room"]]
    x, y = location["positionX"], location["positionY"]
    stale = location.get("staleLocation", False)
    if not (0 <= x < info["roomInfo"]["width"] and 0 <= y < info["roomInfo"]["height"] and not stale):
        global_x = global_y = 0
        found = positions.first(stage, location["room"])
        if found is not None:
            global_x = 256 * found[1] + x
            global_y = 256 * found[0] + y
        for room_name in logic["ROOM_PRIORITY"][stage]:
            if room_name not in rooms_info[stage]:
                raise KeyError(f"{stage}.{room_name} is in ROOM_PRIORITY but not ROOMS_INFO")
            rect = positions.rect(stage, room_name)
            if rect is None:
                continue  # unplaced room: its rectangle is undefined and never matches
            left, top, right, bottom = rect
            if left <= global_x < right and top <= global_y < bottom and not (stale and room_name == location["room"]):
                location["room"] = room_name
                location["positionX"] = global_x - left
                location["positionY"] = global_y - top
                location["staleLocation"] = False
                break
    for region in rooms_info[stage][location["room"]]["regions"]:
        location["section"] = "NONE"
        matched = False
        for requirement in region["requirements"]:
            if _is_valid_requirement(location, requirement):
                _apply_outcome(location, region["outcome"], simplify)
                matched = True
                break
        if matched:
            break


class _Command:
    """One transition: its requirement and outcome split into fast parts."""
    __slots__ = ("req_place", "req_flags", "req_ranges", "req_time", "to_stage", "to_room", "to_section",
                 "sets", "adds", "time_delta")

    def __init__(self, requirement: Dict[str, Any], outcome: Dict[str, Any]):
        self.req_place: List[Tuple[str, str]] = []
        self.req_flags: List[Tuple[str, Any]] = []
        self.req_ranges: List[Tuple[str, Any, Any]] = []
        self.req_time: Optional[Tuple[Any, Any]] = None
        for key, wanted in requirement.items():
            if key in ("stage", "room", "section") and isinstance(wanted, str):
                self.req_place.append((key, wanted))
            elif key == "time" and isinstance(wanted, dict):
                self.req_time = (wanted.get("minimum"), wanted.get("maximum"))
            elif isinstance(wanted, bool) or isinstance(wanted, str):
                self.req_flags.append((key, wanted))
            elif isinstance(wanted, dict):
                self.req_ranges.append((key, wanted.get("minimum"), wanted.get("maximum")))
        self.to_stage = self.to_room = self.to_section = None
        self.sets: List[Tuple[str, Any]] = []
        self.adds: List[Tuple[str, Any, str]] = []
        self.time_delta: Optional[Tuple[str, Any]] = None
        for key, value in outcome.items():
            if key == "stage":
                self.to_stage = value
            elif key == "room":
                self.to_room = value
            elif key == "section":
                self.to_section = value
            elif key in ("positionX", "positionY"):
                continue  # not part of a state's identity and never required
            elif key == "time":
                if isinstance(value, dict):
                    self.time_delta = (value.get("operation"), value.get("value"))
                else:
                    self.time_delta = ("set", value)
            elif isinstance(value, dict):
                self.adds.append((key, value.get("value"), value.get("operation")))
            else:
                self.sets.append((key, value))


def build_logic(rooms: Sequence[Room]) -> Dict[str, Dict[str, List[_Command]]]:
    """getLogic({locationRewards: {}, stageLinks: {}, roomPositions: rooms}, enableElsewhere=true)"""
    logic_data = load("logic")
    rooms_info = logic_data["ROOMS_INFO"]
    positions = _Positions(rooms)
    raw: Dict[str, Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]]] = {}
    for stage, room, _, _ in rooms:
        commands = raw.setdefault(stage, {}).setdefault(room, [])
        info = rooms_info.get(stage, {}).get(room)
        if info is None or "commands" not in info:
            continue
        for command in info["commands"].values():
            for requirement_info in command["requirements"]:
                outcome = dict(command["outcome"])
                requirement: Dict[str, Any] = {}
                for key, value in requirement_info.items():
                    if key == "costs":
                        for cost_key, cost in value.items():
                            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                                requirement[cost_key] = {"minimum": cost}
                                outcome[cost_key] = {"operation": "add", "value": -1 * cost}
                            else:
                                outcome[cost_key] = cost
                    else:
                        requirement[key] = value
                if any(key in outcome for key in _LOCATION_KEYS):
                    location = {
                        "stage": outcome.get("stage", stage),
                        "room": outcome.get("room", room),
                        "section": outcome.get("section", requirement.get("section", "NONE")),
                        "positionX": outcome.get("positionX", 0),
                        "positionY": outcome.get("positionY", 0),
                        "staleLocation": outcome.get("staleLocation", False),
                    }
                    _update_location(location, positions, False)
                    outcome.update(location)
                commands.append((requirement, outcome))
    # Location commands (getLocationRewardCommands with no rewards placed)
    for location_info in logic_data["LOCATIONS"].values():
        for location_requirement in location_info["requirements"]:
            stage = location_requirement["stage"]
            if stage not in logic_data["ROOM_PRIORITY"]:
                continue
            location = {
                "stage": stage,
                "room": location_requirement["room"],
                "section": "NONE",
                "positionX": location_info["outcome"]["positionX"],
                "positionY": location_info["outcome"]["positionY"],
            }
            _update_location(location, positions, True)
            requirement = {k: v for k, v in location_requirement.items() if k not in ("stage", "room")}
            requirement["section"] = location["section"]
            raw.setdefault(stage, {}).setdefault(location_requirement["room"], []).append(
                (requirement, dict(location_info["outcome"])))
    # The hub: links every loading room whose teleporter goes nowhere (no stage links are given)
    hub: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for stage, room_names in logic_data["ROOM_PRIORITY"].items():
        if stage not in raw:
            continue
        for room_name in room_names:
            if not room_name.startswith("loadingRoomTo") or room_name not in raw[stage]:
                continue
            link_count = -1
            for _, outcome in raw[stage][room_name]:
                target = outcome["room"]
                if target.startswith("triggerTeleporterTo"):
                    link_count = len(raw[stage][target])
            if link_count != 0:
                continue
            hub.append(({"stage": "elsewhere", "room": "hub", "debugEnableElsewhere": True},
                        {"stage": stage, "room": room_name, "section": "main"}))
            raw[stage][room_name].append(
                ({"stage": stage, "room": room_name, "section": "main", "debugEnableElsewhere": True},
                 {"stage": "elsewhere", "room": "hub", "section": "main"}))
    raw["elsewhere"] = {"hub": hub}
    return {stage: {room: [_Command(req, out) for req, out in commands] for room, commands in stage_rooms.items()}
            for stage, stage_rooms in raw.items()}


def _flag_key(flags: Dict[str, Any]):
    return frozenset(flags.items())


def validate(logic: Dict[str, Dict[str, List[_Command]]], validation: Dict[str, Any]) -> bool:
    """validate(logic, validation): is the goal state reachable from the starting state (for a
    'required' goal), or not (for a 'forbidden' one)."""
    start = dict(validation["startingState"])
    goal = validation["goalState"]
    stage = start.pop("stage")
    room = start.pop("room")
    section = start.pop("section")
    if "time" not in start:
        raise ValueError("every validation starts with a time budget")
    time = start.pop("time")
    start.pop("positionX", None)
    start.pop("positionY", None)
    found = _search(logic, stage, room, section, start, time, goal)
    if validation["goalType"] == "required":
        return found
    if validation["goalType"] == "forbidden":
        return not found
    return True


def _search(logic, stage: str, room: str, section: str, flags: Dict[str, Any], time: float,
            goal: Dict[str, Any]) -> bool:
    # States: (stage, room, section, flags), most time left first, best time kept per state. The start
    # itself is not recorded, just as the JS leaves it out of its visited map.
    best: Dict[Tuple, float] = {}
    counter = 0
    heap: List[Tuple] = [(-time, counter, (stage, room, section, _flag_key(flags)), flags)]
    while heap:
        neg_time, _, key, flags = heapq.heappop(heap)
        time = -neg_time
        if key in best and best[key] != time:
            continue  # a better time for this state came along
        stage, room, section, _ = key
        for command in logic[stage][room]:
            if not _allowed(command, stage, room, section, flags, time):
                continue
            next_flags = dict(flags)
            next_stage = command.to_stage if command.to_stage is not None else stage
            next_room = command.to_room if command.to_room is not None else room
            next_section = command.to_section if command.to_section is not None else section
            for name, value in command.sets:
                next_flags[name] = value
            for name, value, operation in command.adds:
                if operation == "replace":
                    next_flags[name] = value
                elif operation == "add":
                    next_flags[name] = next_flags.get(name, 0) + value
            next_time = time
            if command.time_delta is not None:
                operation, value = command.time_delta
                if operation == "add":
                    next_time = time + value  # the same float steps as the JS's `state.time += -cost`
                elif operation in ("replace", "set"):
                    next_time = value
            _simplify(next_flags)
            if next_section == "NONE":
                raise ValueError(f"no section for {next_stage}.{next_room} (the JS throws here too)")
            next_key = (next_stage, next_room, next_section, _flag_key(next_flags))
            if next_key not in best or best[next_key] < next_time:
                best[next_key] = next_time
                state = dict(next_flags)
                state["stage"] = next_stage
                state["room"] = next_room
                state["section"] = next_section
                state["time"] = next_time
                if _is_valid_requirement(state, goal):
                    return True
                counter += 1
                heapq.heappush(heap, (-next_time, counter, next_key, next_flags))
    return False


def _allowed(command: _Command, stage: str, room: str, section: str, flags: Dict[str, Any], time: float) -> bool:
    for key, wanted in command.req_place:
        have = stage if key == "stage" else room if key == "room" else section
        if have != wanted:
            return False
    if command.req_time is not None:
        minimum, maximum = command.req_time
        if minimum is not None and time < minimum:
            return False
        if maximum is not None and time > maximum:
            return False
    for key, wanted in command.req_flags:
        if isinstance(wanted, bool):
            have = flags.get(key, False)
            if not (isinstance(have, bool) and have == wanted):
                return False
        elif flags.get(key, "NONE") != wanted:
            return False
    for key, minimum, maximum in command.req_ranges:
        have = flags.get(key, 0)
        if minimum is not None and have < minimum:
            return False
        if maximum is not None and have > maximum:
            return False
    return True


def stage_is_valid(stage_name: str, rooms: Sequence[Room]) -> bool:
    """Runs every validation for the stage (stops at the first failure, like Array.every)."""
    validations = load("logic")["VALIDATIONS"].get(stage_name)
    if not validations:
        return True
    logic = build_logic(rooms)
    return all(validate(logic, validation) for validation in validations)
