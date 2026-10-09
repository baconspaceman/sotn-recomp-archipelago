from typing import ClassVar, Dict, Tuple, Any, List, Optional, TextIO

import logging, typing, os
from worlds.AutoWorld import WebWorld, World
from BaseClasses import Tutorial, MultiWorld, ItemClassification, Item
from Options import AssembleOptions

from .Items import SotnItem, items, relic_table, item_id_to_name, JUNK_ITEMS, swap_in_powerful
from .Locations import locations, SotnLocation
from .Regions import create_regions, create_regions_no_logic
from .CastleRegions import create_castle_regions, castle_logic_problem
from .Rules import set_rules, set_no_logic_rules
from .Options import SOTNOptions, sotn_option_groups
from .Rom import SotnPatchData, write_tokens, start_passage
from .Recomp import recomp_payload
from .Logic import export_logic
from .Groups import ITEM_GROUPS, LOCATION_GROUPS
from . import castle
from .data.Constants import GAME_NAME


# Thanks for Fuzzy for Archipelago Manual it all started there
# Thanks for Wild Mouse for it´s randomizer and a lot of stuff over here
# Thanks for TalicZealot with a lot of rom addresses
# Thanks for all decomp folks
# I wish I have discovered most of those earlier, would save me a lot of RAM searches
# Thanks for all the help from the folks at Long Library and AP Discords.

class SotnWeb(WebWorld):
    setup = Tutorial(
        "Multiworld Setup Guide",
        "A guide to setting up Symphony of the Night on SymphonyRecomp for MultiWorld.",
        "English",
        "setup_en.md",
        "setup/en",
        ["Arcadia64"]
    )

    tutorials = [setup]
    option_groups = sotn_option_groups


EXTRA_ADD = ["Duplicator", "Crissaegrim", "Ring of varda", "Mablung sword", "Masamune", "Marsil", "Yasutsuna"]


# Castle layouts tried before generation gives up (rerolls are rare).
CASTLE_ATTEMPTS = 50

# The areas a shuffled castle's links name (castle/ stage links: from<Area>To<Area>).
AREA_NAMES = {
    "AbandonedMine": "Abandoned Mine", "AlchemyLaboratory": "Alchemy Laboratory", "CastleEntrance": "Castle Entrance",
    "CastleKeep": "Castle Keep", "Catacombs": "Catacombs", "ClockTower": "Clock Tower", "Colosseum": "Colosseum",
    "LongLibrary": "Long Library", "MarbleGallery": "Marble Gallery", "OlroxsQuarters": "Olrox's Quarters",
    "OuterWall": "Outer Wall", "RoyalChapel": "Royal Chapel", "UndergroundCaverns": "Underground Caverns",
    "WarpRooms": "Warp Rooms",
}


def area_links(layout: castle.CastleLayout) -> List[str]:
    """The castle's area connections for the spoiler log, one per line: each area's door (named by where
    it leads in the vanilla castle) and the door it now leads to."""
    def door(link: str) -> str:
        for key, name in AREA_NAMES.items():
            prefix = f"from{key}To"
            if link.startswith(prefix) and link[len(prefix):] in AREA_NAMES:
                return f"{name} (door to {AREA_NAMES[link[len(prefix):]]})"
        return link

    pairs = {tuple(sorted((door(a), door(b)))) for a, b in layout.stage_links.items()}
    return [f"{a} <-> {b}" for a, b in sorted(pairs)]


# Set to write each slot's disc writes (token_data format) next to the seed, for tools/verify_payload.py.
DEBUG_TOKENS_ENV = "SOTN_RECOMP_DEBUG_TOKENS"


class SotnWorld(World):
    """
    Castlevania: Symphony of the Night (Konami, 1997), played on SymphonyRecomp, the native PC version
    of the PlayStation game, with the Archipelago mod. Explore Dracula's castle as Alucard; relics and
    items are shuffled across the multiworld.
    """
    game: ClassVar[str] = GAME_NAME
    web: ClassVar[WebWorld] = SotnWeb()
    options_dataclass = SOTNOptions
    options: SOTNOptions
    data_version: ClassVar[int] = 1
    required_client_version: Tuple[int, int, int] = (0, 4, 5)
    extra_add: List[str]
    sotn_patch: SotnPatchData
    castle_layout: Optional[castle.CastleLayout]

    item_name_groups = ITEM_GROUPS
    location_name_groups = LOCATION_GROUPS

    item_name_to_id: ClassVar[Dict[str, int]] = {name: data["id"] for name, data in items.items()}
    location_name_to_id: ClassVar[Dict[str, int]] = {name: data["ap_id"] for name, data in locations.items()}

    def __init__(self, world: MultiWorld, player: int):
        super().__init__(world, player)

    @classmethod
    def stage_assert_generate(cls, _multiworld: MultiWorld) -> None:
        pass

    def generate_early(self) -> None:
        # Per world: upstream kept this as a class list and popped from it, so a second SotN slot
        # (or the next generation in the same process) got fewer powerful items.
        self.extra_add = list(EXTRA_ADD)
        # A shuffled castle (Sestren's SOTN Shuffler, castle/), laid out before the regions are made from it.
        self.castle_layout = None
        if self.options.shuffle_rooms.value or self.options.shuffle_area_connections.value:
            for _ in range(CASTLE_ATTEMPTS):
                self.castle_layout = castle.generate(
                    castle.seed_name(self.random),
                    shuffle_rooms=bool(self.options.shuffle_rooms.value),
                    shuffle_stages=bool(self.options.shuffle_area_connections.value),
                    area_colors=bool(self.options.map_area_colors.value))
                problem = self.castle_problem()
                if problem is None:
                    break
                logging.debug(f"{self.player_name}: castle layout {self.castle_layout.seed} rerolled: {problem}")
            else:
                raise Exception(f"{self.player_name}: no usable castle layout in {CASTLE_ATTEMPTS} tries")

    def castle_problem(self) -> Optional[str]:
        """Why this castle layout can't be used for this world, or None."""
        layout = self.castle_layout
        if self.options.starting_area.value:
            # The passage to the start (Rom.py start_room_rando) takes two squares by the first room; the game
            # finds a square's room in table order, so room 1 (the only one before them) mustn't be there.
            x, y = start_passage(self)
            top, left = layout.positions["castleEntrance"]["dropUnderPortcullis"]  # 1x2 squares
            if x + 1 > 63 or (left in (x, x + 1) and top <= y <= top + 1):
                return "no room for the passage to the starting area"
        return castle_logic_problem(self)

    def create_item(self, name: str) -> Item:
        data = items[name]
        classification = data["classification"]
        # Upstream changed the shared item table, making Faerie scroll progression for every SotN slot.
        if name == "Faerie scroll" and self.options.enemysanity.value and self.options.enemysanity_needs_faerie_scroll.value:
            classification = ItemClassification.progression
        return SotnItem(name, classification, data["id"], self.player)

    def create_items(self) -> None:
        added_items = 1  # "Reverse Center Cube - Kill Dracula"
        itempool: typing.List[SotnItem] = []
        active_locations = self.multiworld.get_unfilled_locations(self.player)
        total_location = len(active_locations)

        loc = self.multiworld.get_location("Reverse Center Cube - Kill Dracula", self.player)
        loc.place_locked_item(self.create_event("Victory"))

        self.multiworld.completion_condition[self.player] = lambda state: state.has("Victory", self.player)

        # Add progression items
        itempool += [self.create_item("Spike breaker")]
        itempool += [self.create_item("Holy glasses")]
        itempool += [self.create_item("Gold ring")]
        itempool += [self.create_item("Silver ring")]
        added_items += 4
        added_list = ["Spike breaker", "Holy glasses", "Gold ring", "Silver ring"]
        vanilla_list = []

        # Add relics
        for r in relic_table.keys():
            itempool += [self.create_item(r)]
            added_items += 1
            added_list.append(r)

        for loc in active_locations:
            if loc.name == "Reverse Center Cube - Kill Dracula":
                continue
            if "Enemysanity" in loc.name:
                continue

            vanilla_item = locations[loc.name]["vanilla_item"]
            vanilla_list.append(vanilla_item)

        for added in added_list:
            vanilla_list.remove(added)

        if self.options.powerful_items.value:
            swap_in_powerful(self.random, vanilla_list, self.extra_add)

        for item in vanilla_list:
            itempool += [self.create_item(item)]
            added_items += 1

        if self.options.enemysanity.value:
            # Enemysanity adds 141 locations.
            # TODO: Add an option to customize extra locations
            extra_vessels = 0
            extra_equips = 0
            if self.options.difficulty.value == 0:
                extra_vessels = 50
                extra_equips = 50
                itempool += [self.create_item("Spike breaker")]
                itempool += [self.create_item("Holy glasses")]
                itempool += [self.create_item("Gold ring")]
                itempool += [self.create_item("Silver ring")]
                added_items += 4

                for r in relic_table.keys():
                    itempool += [self.create_item(r)]
                    added_items += 1
            elif self.options.difficulty.value == 1:
                extra_equips = 35
                extra_vessels = 35
                itempool += [self.create_item("Spike breaker")]
                itempool += [self.create_item("Holy glasses")]
                itempool += [self.create_item("Gold ring")]
                itempool += [self.create_item("Silver ring")]
                added_items += 4
            elif self.options.difficulty.value == 2:
                extra_equips = 15
                extra_vessels = 15

            added_equip = 0
            while added_items < total_location and added_equip < extra_equips:
                rng_item = self.random.choice([i for i in range(1, 259) if i not in [126, 169, 195, 217, 226]])
                itempool += [self.create_item(item_id_to_name[rng_item])]
                added_items += 1
                added_equip += 1

            added_vessel = 0
            while added_items < total_location and added_vessel < extra_vessels:
                rng_vessel = self.random.choice([412, 423])
                itempool += [self.create_item(item_id_to_name[rng_vessel])]
                added_items += 1
                added_vessel += 1

        # Still have space? Add junk items
        itempool += [self.create_random_junk() for _ in range(total_location - added_items)]

        self.multiworld.itempool += itempool

    def create_random_junk(self) -> SotnItem:
        return self.create_item(self.get_filler_item_name())

    def get_filler_item_name(self) -> str:
        # Also what AP fills with (start_inventory_from_pool, item links): never a token or an event.
        return self.random.choice(JUNK_ITEMS)

    def create_regions(self) -> None:
        if self.options.no_logic.value:
            create_regions_no_logic(self.multiworld, self.player, self.options)
        elif self.castle_layout is not None:
            create_castle_regions(self)
        else:
            create_regions(self.multiworld, self.player, self.options)

    def create_event(self, name: str) -> Item:
        return SotnItem(name, ItemClassification.progression, None, self.player)

    def set_rules(self):
        if self.options.no_logic.value:
            set_no_logic_rules(self.multiworld, self.player, self.options)
        else:
            set_rules(self.multiworld, self.player, self.options)

    def pre_output(self) -> None:
        # The game changes, worked out once (with this world's random rolls) for fill_slot_data. Not in
        # post_fill: progression balancing runs after that and can still move items, and the mod takes this
        # world's own items from these writes (the server doesn't send a player their own items).
        self.sotn_patch = SotnPatchData()
        write_tokens(self, self.sotn_patch)

    def fill_slot_data(self) -> Dict[str, Any]:
        option_names: List[str] = [option_name for option_name in self.options_dataclass.type_hints
                                   if option_name != "plando_items"]
        slot_data = self.options.as_dict(*option_names)
        # The names PopTracker's SotN pack reads (the upstream world's).
        slot_data["open_no4"] = self.options.caverns_back_door.value
        slot_data["open_are"] = self.options.colosseum_back_door.value
        slot_data["boss_locations"] = self.options.boss_drops.value
        # The patch's writes for the SymphonyRecomp mod, which applies them to RAM as files load.
        slot_data["recomp"] = recomp_payload(self.sotn_patch, self.castle_layout.writes if self.castle_layout else None)
        if self.castle_layout:
            # What the castle was made from: castle.generate() with these gives the same writes again.
            layout = self.castle_layout
            slot_data["recomp"]["castle"] = {"seed": layout.seed, "shuffle_rooms": layout.shuffle_rooms,
                                             "shuffle_stages": layout.shuffle_stages, "area_colors": layout.area_colors}
        # The logic as data, for the mod's map (Logic.py).
        slot_data["recomp"]["logic"] = export_logic(self.multiworld, self.player)
        return slot_data

    def write_spoiler(self, spoiler_handle: TextIO) -> None:
        if self.castle_layout is None or not self.castle_layout.shuffle_stages:
            return
        spoiler_handle.write(f"\n\nArea connections ({self.multiworld.get_player_name(self.player)}):\n\n")
        for line in area_links(self.castle_layout):
            spoiler_handle.write(f"    {line}\n")

    def generate_output(self, output_directory: str) -> None:
        # Nothing to hand out: the mod gets everything from slot_data when it connects.
        if os.environ.get(DEBUG_TOKENS_ENV):
            out_file_name = self.multiworld.get_out_file_name_base(self.player)
            with open(os.path.join(output_directory, f"{out_file_name}.sotn_tokens"), "wb") as f:
                f.write(self.sotn_patch.get_token_binary())
