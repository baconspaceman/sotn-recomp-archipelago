"""Lists the code sites the castle shuffler leaves to the mod, and checks that none of its data writes
land inside a function.

usage: python -I tools/castle/code_sites.py [--seed NAME] [--config ref/SymphonyRecomp/config/sotn.json]

For one seed: every value BIN-Patcher would write into code (file, offset, RAM address, function,
the rule it comes from, the value), and the fixed code changes inside the normalize patches.
The function ranges come from SymphonyRecomp's function maps.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apworld", "sotn_recomp"))

import castle  # noqa: E402
from castle.patcher import dependencies  # noqa: E402


def load_functions(config_path):
    """file key -> (RAM base, [(start, end, name)])"""
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    result = {}
    for overlay in config["overlays"]:
        key = os.path.basename(overlay["file"]).split(".")[0].upper()
        if key in result:
            continue
        path = os.path.join(os.path.dirname(config_path), overlay["funcMap"])
        functions = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for fn in json.load(f)["functions"]:
                    start = int(fn["address"], 16)
                    functions.append((start, start + int(fn.get("size", 4)), fn["name"]))
        result[key] = (int(overlay["base"], 16), sorted(functions))
    return result


def function_at(functions, key, offset):
    if key not in functions:
        return None, None
    base, ranges = functions[key]
    ram = base + offset
    for start, end, name in ranges:
        if start <= ram < end:
            return ram, name
    return ram, None


def formulas(actions):
    """Each value a transformation sets, as a readable expression: {target: expression}"""
    result = {}
    text = ""
    for action in actions:
        operand = action.get("property", action.get("constant"))
        if action["action"] == "get":
            text = str(operand)
        elif action["action"] == "add":
            text = f"{text} + {operand}"
        elif action["action"] == "subtract":
            text = f"{text} - {operand}"
        elif action["action"] == "multiply":
            text = f"({text}) * {operand}"
        elif action["action"] == "set":
            result[action.get("name") or action.get("property")] = text
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default="TestSeedA")
    parser.add_argument("--config", default=os.path.join(ROOT, "ref", "SymphonyRecomp", "config", "sotn.json"))
    args = parser.parse_args()
    functions = load_functions(args.config)
    layout = castle.generate(args.seed)

    rules = {}
    for actions in dependencies():
        rules.update(formulas(actions))
    print(f"Code sites for seed {args.seed} (rooms and stages shuffled):")
    for site in layout.code_sites:
        ram, name = function_at(functions, site.file, site.offset)
        rule = rules.get(site.name) or rules.get(f"{site.name}.data")
        ram_text = f"0x{ram:08X}" if ram is not None else "?"
        print(f"  {site.name:52} {site.file:4} +0x{site.offset:05X} {ram_text} {name or '-':36} "
              f"{site.type:4} {site.value:6}  = {rule or '?'}")

    print("Fixed code changes in the patches (not written; the mod does these itself):")
    fixed = castle.load("fixed")
    for group in ("powerOfWolf", "normalize", "mapColors"):
        for site in fixed[group]["codeSites"]:
            first = site["bytes"][0][0]
            ram, name = function_at(functions, site["file"], first)
            data = bytes(b for _, b in site["bytes"]).hex()
            ram_text = f"0x{ram:08X}" if ram is not None else "?"
            print(f"  {site['path']:76} {site['file']:4} +0x{first:05X} {ram_text} {name or '-':28} {data}")

    inside = []
    for key, bytes_at in layout.writes.items():
        for offset in bytes_at:
            ram, name = function_at(functions, key, offset)
            if name is not None:
                inside.append((key, offset, name))
    print(f"data writes inside a function: {len(inside)}")
    for key, offset, name in inside[:20]:
        print(f"  {key} +0x{offset:X} {name}")


if __name__ == "__main__":
    main()
