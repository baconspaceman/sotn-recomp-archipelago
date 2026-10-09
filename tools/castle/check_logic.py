"""Checks the shuffled castle's logic (castle/aplogic.py) on many castles, without Archipelago.

For the vanilla castle and for seeds in each shuffle mode (rooms, areas, both) and option set, with every
location (item_pool everything, boss drops, enemysanity): whether every location is reachable with every
progression item, how many with none, and how often the world would reroll the castle (aplogic.problem,
counting free locations by the graph alone: the world also applies Rules.py there). Also checks the inverted
kit: what the first castle's places it doesn't open from the Keep (where the castle turns over) need.

  python tools/castle/check_logic.py [--seeds 30] [--jobs 8] [--verbose]
"""
import argparse
import enum
import os
import sys
import time
import types
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))

OPTION_SETS = {
    "default": dict(caverns_back_door=0, colosseum_back_door=False, open_shortcuts=False),
    "back doors": dict(caverns_back_door=2, colosseum_back_door=True, open_shortcuts=False),
    "caverns door after": dict(caverns_back_door=1, colosseum_back_door=False, open_shortcuts=False),
    "shortcuts": dict(caverns_back_door=0, colosseum_back_door=False, open_shortcuts=True),
}
MODES = {"rooms": (True, False), "areas": (False, True), "both": (True, True)}


def load_world():
    """The world's Locations, Items and castle package, without Archipelago."""
    apworld = os.path.join(ROOT, "apworld")
    sys.path.insert(0, apworld)
    base = types.ModuleType("BaseClasses")
    base.Location = type("Location", (), {})
    base.Item = type("Item", (), {})
    base.ItemClassification = enum.IntFlag("ItemClassification", "filler progression useful trap skip_balancing")
    sys.modules["BaseClasses"] = base
    import importlib
    import importlib.util
    # The package without running its __init__ (which needs Archipelago); its spec lets pkgutil read data
    folder = os.path.join(apworld, "sotn_recomp")
    pkg = types.ModuleType("sotn_recomp")
    pkg.__path__ = [folder]
    pkg.__file__ = os.path.join(folder, "__init__.py")
    pkg.__spec__ = importlib.util.spec_from_file_location("sotn_recomp", os.path.join(folder, "__init__.py"),
                                                          submodule_search_locations=[folder])
    sys.modules["sotn_recomp"] = pkg
    return (importlib.import_module("sotn_recomp.Locations"), importlib.import_module("sotn_recomp.Items"),
            importlib.import_module("sotn_recomp.castle"), importlib.import_module("sotn_recomp.castle.aplogic"))


def world_locations(locations_mod):
    """Every location (item_pool everything, boss drops and enemysanity on), and the Kill Dracula event."""
    return sorted(locations_mod.locations) + ["Reverse Center Cube - Kill Dracula"]


def check(job):
    mode, seed, option_set = job
    locations_mod, items_mod, castle, aplogic = load_world()
    names = world_locations(locations_mod)
    progression = set(items_mod.progression_items)
    start = time.perf_counter()
    if mode == "vanilla":
        layout = aplogic.vanilla_layout()
    else:
        layout = castle.generate(seed, *MODES[mode], area_colors=False, link_labels=False)
    made = time.perf_counter()
    graph = aplogic.build_graph(layout, aplogic.Options(**OPTION_SETS[option_set]), names)
    verdict = aplogic.problem(graph, progression)
    done = time.perf_counter()
    missing = sorted(set(names) - aplogic.reachable(graph, progression))
    free = len(aplogic.reachable(graph, ()))
    # The inverted kit, from where the castle turns over (with what only the first castle needs): what else the
    # places it doesn't open there need
    kit = set(aplogic.INVERTED_KIT) | aplogic.NOT_IN_INVERTED | {aplogic.EVENTS["statusRichterSaved"]}
    everywhere = aplogic.reach(graph, set(progression))
    with_kit = aplogic.reach(graph, set(kit), aplogic.INVERTED_ENTRY)
    kit_misses = [n for n in everywhere - with_kit if n[0] != "castleEntrance" and n != aplogic.INVERTED]
    kit_needs = sorted(item for item in progression - kit
                       if set(kit_misses) & aplogic.reach(graph, kit | {item}, aplogic.INVERTED_ENTRY))
    return dict(mode=mode, seed=seed, options=option_set, verdict=verdict, missing=missing, free=free,
                unplaced=graph.unplaced, nodes=len(graph.nodes), edges=len(graph.edges), kit_misses=len(kit_misses),
                kit_needs=kit_needs, generate=made - start, logic=done - made)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--jobs", type=int, default=os.cpu_count())
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    jobs = [("vanilla", "", o) for o in OPTION_SETS]
    jobs += [(mode, f"check{i}", o) for mode in MODES for i in range(args.seeds) for o in OPTION_SETS]
    with ProcessPoolExecutor(args.jobs) as pool:
        results = list(pool.map(check, jobs))
    failed = 0
    for r in results:
        if r["mode"] == "vanilla":
            print(f"vanilla [{r['options']}]: {r['nodes']} places, {r['edges']} ways, {r['free']} locations free, "
                  f"verdict {r['verdict']}")
            failed += bool(r["verdict"])
    needs = {}
    for r in results:
        for item in r["kit_needs"]:
            needs[item] = needs.get(item, 0) + 1
    print(f"inverted kit: the places it doesn't open from the Keep need {needs or 'nothing'} (in so many castles)")
    failed += sum(1 for r in results if r["kit_misses"] and not r["verdict"])
    for mode in MODES:
        for option_set in OPTION_SETS:
            rs = [r for r in results if r["mode"] == mode and r["options"] == option_set]
            reasons = {}
            for r in rs:
                if r["verdict"]:
                    reason = r["verdict"].split(",")[0].split(" ", 1)[1] if r["verdict"][0].isdigit() else r["verdict"]
                    reason = reason.split(" at ")[0]
                    reasons[reason] = reasons.get(reason, 0) + 1
            frees = sorted(r["free"] for r in rs)
            rerolled = sum(1 for r in rs if r["verdict"])
            castle = sum(r["generate"] for r in rs) / len(rs)
            logic = sum(r["logic"] for r in rs) / len(rs)
            print(f"{mode:5} [{option_set}]: {rerolled}/{len(rs)} rerolled {reasons}; locations free: min {frees[0]}, "
                  f"median {frees[len(frees) // 2]}; castle {castle:.2f}s, logic {logic:.3f}s")
            if args.verbose:
                for r in rs:
                    if r["verdict"]:
                        print(f"    {r['seed']}: {r['verdict']}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
