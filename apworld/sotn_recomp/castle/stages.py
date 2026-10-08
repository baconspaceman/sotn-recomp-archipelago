"""Stage links (teleporters between stages) and the stage arrangement on the map: shuffle-stages.js
shuffleStages, getVanillaStageLinks, getTeleporterChanges, and common.js arrangeStages."""
from typing import Dict, List, Optional, Tuple

from .data import load
from .jsrandom import SeedRandom, shuffle_in_place
from .rooms import Group, combine, fits


def _cap(name: str) -> str:
    return name[:1].upper() + name[1:]


def vanilla_stage_links() -> Dict[str, str]:
    """getVanillaStageLinks: every teleporter leads back to the stage it came from."""
    teleporters = load("shuffler")["TELEPORTERS"]
    result = {}
    for name in load("shuffler")["LINKS"]:
        info = teleporters[name]
        result[name] = f"from{_cap(info['targetStage'])}To{_cap(info['sourceStage'])}"
    return result


def shuffle_stages(seed: str) -> Dict[str, str]:
    """shuffleStages(seed).links: pairs every teleporter with another one, starting from the Castle
    Entrance exit to the Alchemy Laboratory and working outwards, retrying until all are paired."""
    links_info = load("shuffler")["LINKS"]
    stage_links = load("shuffler")["STAGE_LINKS"]
    forbidden = {name: set(info["forbiddenConnections"]) for name, info in links_info.items()}
    rng = SeedRandom(seed)
    while True:
        valid = True
        links: Dict[str, Optional[str]] = {name: None for name in links_info}
        linked = {stage: set() for stage in stage_links}
        remaining = dict.fromkeys(links_info)  # an insertion-ordered set
        work = dict.fromkeys(["fromCastleEntranceToAlchemyLaboratory"])
        while work:
            source_name = shuffle_in_place(rng, sorted(work))[0]
            del work[source_name]
            source = links_info[source_name]
            possible = []
            for target_name, target in links_info.items():
                if (
                    target_name in remaining
                    and len(stage_links[source["stage"]]) >= target["minimumTargetLinkCount"]
                    and len(stage_links[target["stage"]]) >= source["minimumTargetLinkCount"]
                    and source["stage"] != target["stage"]
                    and source["direction"] != target["direction"]
                    and target_name not in forbidden[source_name]
                    and source_name not in forbidden[target_name]
                    and target["stage"] not in linked[source["stage"]]
                    and source["stage"] not in linked[target["stage"]]
                    # Colosseum and Long Library must not connect to the same stage
                    and not (
                        ("longLibrary" in linked[source["stage"]] and target["stage"] == "catacombs")
                        or ("longLibrary" in linked[target["stage"]] and source["stage"] == "catacombs")
                        or ("catacombs" in linked[source["stage"]] and target["stage"] == "longLibrary")
                        or ("catacombs" in linked[target["stage"]] and source["stage"] == "longLibrary")
                    )
                ):
                    possible.append(target_name)
            if not possible:
                valid = False
                break
            target_name = shuffle_in_place(rng, sorted(possible))[0]
            target = links_info[target_name]
            links[target_name] = source_name
            links[source_name] = target_name
            linked[source["stage"]].add(target["stage"])
            linked[target["stage"]].add(source["stage"])
            remaining.pop(source_name, None)
            remaining.pop(target_name, None)
            work.pop(source_name, None)
            work.pop(target_name, None)
            for next_name in remaining:
                if links_info[next_name]["stage"] == target["stage"]:
                    work[next_name] = None
        if remaining:
            valid = False
        if valid:
            return links  # type: ignore[return-value]


def teleporter_changes(links: Dict[str, str]) -> Dict[str, Dict[str, int]]:
    """getTeleporterChanges: for each linked teleporter, the destination fields of its partner's return
    trip, so going through A lands where coming back through B would. Castle Entrance's teleporters
    exist twice (the stage file is swapped after the Alchemy Laboratory). Values are the raw table
    words, which is what BIN-Patcher writes for the aliases the JS passes."""
    fact = load("facts")["arrays"]["teleporters"]
    aliases = fact["aliases"]
    entries = fact["entries"]
    result: Dict[str, Dict[str, int]] = {}
    for source_name, target_name in links.items():
        source = entries[aliases[source_name]]
        target = entries[aliases[target_name]]
        # (from, to) of this teleporter, and (to, from) of its partner: its return trip
        assignments = [(source["sourceStageId"], source["targetStageId"],
                        target["targetStageId"], target["sourceStageId"])]
        if source["sourceStageId"] == "castleEntrance":
            assignments.append(("castleEntranceRevisited", source["targetStageId"],
                                target["targetStageId"], target["sourceStageId"]))
        for source_from, source_to, target_from, target_to in assignments:
            if source_to == "castleEntranceRevisited":
                source_to = "castleEntrance"
            name_a = f"from{_cap(source_from)}To{_cap(source_to)}"
            name_b = f"from{_cap(target_from)}To{_cap(target_to)}"
            if name_a not in aliases or name_b not in aliases:
                raise KeyError(f"teleporter {name_a} or {name_b} is not in the table")
            raw = entries[aliases[name_b]]["raw"]
            result[name_a] = {
                "playerX": raw["playerX"],
                "playerY": raw["playerY"],
                "roomOffset": raw["roomOffset"],
                "targetStageId": raw["targetStageId"],
            }
    return result


def arrange_stages(seed: str, groups: List[Group]) -> Group:
    """arrangeStages: drops the stages onto the map one at a time in random order, each at the spot
    (or a random one of the spots) that keeps the map's bounding box smallest.

    The JS tries combineNodeGroups at every offset; this finds the smallest box first and only tests
    offsets that could match it, then takes the valid ones in the JS's loop order."""
    rng = SeedRandom(seed)
    valid = False
    result = groups[0]
    while not valid:
        valid = True
        indexes = list(range(len(groups)))
        shuffle_in_place(rng, indexes)
        result = groups[indexes.pop()]
        while indexes:
            group = groups[indexes.pop()]
            candidates = _best_placements(result, group)
            if not candidates:
                # The JS flags the attempt and carries on with the next stage (`continue` in the inner loop)
                valid = False
                continue
            row_offset, col_offset = candidates[rng.floor_mul(len(candidates))]
            result = combine(result, group, row_offset, col_offset)
    return result


def _best_placements(result: Group, group: Group) -> List[Tuple[int, int]]:
    rows_r, cols_r = len(result.masks), result.width
    rows_g, cols_g = len(group.masks), group.width
    row_offsets = range(-rows_g, rows_r + rows_g + 1)
    col_offsets = range(-cols_g, cols_r + cols_g + 1)
    height = {ro: max(rows_r, rows_g + ro) - min(0, ro) for ro in row_offsets}
    width = {co: max(cols_r, cols_g + co) - min(0, co) for co in col_offsets}
    by_area: Dict[int, List[Tuple[int, int]]] = {}
    for ro in row_offsets:
        for co in col_offsets:
            by_area.setdefault(height[ro] * width[co], []).append((ro, co))
    for area in sorted(by_area):
        found = [(ro, co) for ro, co in by_area[area] if fits(result, group, ro, co)]
        if found:
            return found  # already in the JS's loop order (rows outer, columns inner)
    return []
