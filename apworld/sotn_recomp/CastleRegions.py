"""Regions for a shuffled castle (castle/aplogic.py's graph), in place of Regions.py's vanilla ones.

Every place of the castle (a section of a room) is a region, named "<area> - <room> (<section>)"; the room
logic's moves are its entrances; Sestren's status flags (switches, shortcuts, warp rooms, Richter) are
events. A location with one spot and nothing more to it is in that spot's region; one with several spots
(an enemy kind, an item both Castle Entrances have) or with something more to it (the Gold ring after the
Succubus, the inverted castle's kit) gets a region of its own, entered from each spot. Its own rules are
still Rules.py's, set on top.

Every rule here calls state.has with one item at a time (Logic.py reads the rules by probing has()).
"""
from typing import Callable, Dict, List, Optional, Set

from BaseClasses import Region

from .Locations import SotnLocation, locations, LOCATION_TO_ABREV
from .Items import progression_items
from .Rules import set_rules
from .data.Constants import EXTENSIONS
from .castle import aplogic


def world_location_names(options) -> List[str]:
    """The world's locations, as Regions.create_regions picks them (and the Kill Dracula event)."""
    extension = options.item_pool.value
    names = []
    for name, data in locations.items():
        abrev = LOCATION_TO_ABREV[name]
        if (abrev in EXTENSIONS[extension] or (options.boss_drops.value and data.get("boss")) or
                (options.enemysanity.value and data.get("enemy"))):
            names.append(name)
    return names + [aplogic.KILL_DRACULA]


def logic_options(options) -> aplogic.Options:
    return aplogic.Options(caverns_back_door=options.caverns_back_door.value,
                           colosseum_back_door=bool(options.colosseum_back_door.value),
                           open_shortcuts=bool(options.open_shortcuts.value))


def castle_graph(world) -> aplogic.Graph:
    return aplogic.build_graph(world.castle_layout, logic_options(world.options), world_location_names(world.options))


class _NoItems:
    """A state with nothing in it, for asking Rules.py's rules what they need."""
    @staticmethod
    def has(item: str, player: int, count: int = 1) -> bool:
        return False


def free_to_hold_progression(world, names: List[str]) -> Set[str]:
    """The locations whose own rules (Rules.py) need nothing and whose item rules let them hold this world's
    progression: Rules.set_rules is run on stand-ins of the locations, as it will be on the real ones."""
    player = world.player
    spots = {name: SotnLocation(player, name, None, None) for name in names}

    class Locations:
        @staticmethod
        def get_location(name: str, _player: int) -> SotnLocation:
            return spots[name]

    set_rules(Locations, player, world.options)
    progression = world.create_item("Spike breaker")
    return {name for name, spot in spots.items()
            if spot.access_rule(_NoItems) and spot.item_rule(progression)}


def castle_logic_problem(world) -> Optional[str]:
    """Why the world's shuffled castle can't be used, or None: a location out of reach even with every item,
    the inverted castle cut off from its entrance, or no location that can hold progression in reach with no
    items (with no_logic, only the first two)."""
    graph = castle_graph(world)
    every_item = set(progression_items) | {"Faerie scroll"}
    if world.options.no_logic.value:
        return aplogic.problem(graph, every_item, 0)
    free = free_to_hold_progression(world, world_location_names(world.options))
    return aplogic.problem(graph, every_item, aplogic.MIN_FREE_LOCATIONS, free.__contains__)


def has_rule(dnf: aplogic.DNF, player: int) -> Optional[Callable]:
    """A rule for a DNF (any one set of items), or None for always."""
    if dnf == aplogic.ALWAYS:
        return None
    sets = [tuple(sorted(s)) for s in dnf]
    if len(sets) == 1:
        (only,) = sets
        if len(only) == 1:
            (item,) = only
            return lambda state: state.has(item, player)
        return lambda state: all(state.has(i, player) for i in only)
    return lambda state: any(all(state.has(i, player) for i in s) for s in sets)


def create_castle_regions(world) -> None:
    multiworld, player = world.multiworld, world.player
    graph = castle_graph(world)
    aplogic.prune(graph, set(progression_items) | {"Faerie scroll"})  # what can't be had even with everything goes

    regions: Dict[aplogic.Node, Region] = {}

    def region(node: aplogic.Node) -> Region:
        if node not in regions:
            regions[node] = Region(aplogic.node_name(node), player, multiworld)
            multiworld.regions.append(regions[node])
        return regions[node]

    menu = Region("Menu", player, multiworld)
    multiworld.regions.append(menu)
    menu.connect(region(graph.start))

    for (a, b), dnf in sorted(graph.edges.items()):
        region(a).connect(region(b), f"{aplogic.node_name(a)} -> {aplogic.node_name(b)}", has_rule(dnf, player))

    def place(name: str, places, make) -> None:
        """Puts a location where it can be had: in its spot's region, or in a region of its own entered
        from each of its spots."""
        if not places:
            raise ValueError(f"{name} can't be reached in castle {world.castle_layout.seed}")
        if len(places) == 1 and places[0][1] == aplogic.ALWAYS:
            parent = region(places[0][0])
        else:
            parent = Region(f"{name} (where)", player, multiworld)
            multiworld.regions.append(parent)
            for node, dnf in places:
                region(node).connect(parent, f"{aplogic.node_name(node)} -> {name}", has_rule(dnf, player))
        parent.locations.append(make(parent))

    for event, grants in sorted(graph.events.items()):
        def make_event(parent, event=event):
            location = SotnLocation(player, event, None, parent)
            location.place_locked_item(world.create_event(event))
            return location
        place(event, grants, make_event)

    for name, places in sorted(graph.locations.items()):
        address = None if name == aplogic.KILL_DRACULA else locations[name]["ap_id"]
        place(name, places, lambda parent, name=name, address=address: SotnLocation(player, name, address, parent))
