"""Times castle.generate() over a number of seeds (one process, data already loaded).

usage: python -I tools/castle/bench.py [--count 20] [--modes both,rooms,stages] [--prefix Bench]
"""
import argparse
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "apworld", "sotn_recomp"))

import castle  # noqa: E402

MODES = {"both": (True, True), "rooms": (True, False), "stages": (False, True)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--modes", default="both,rooms,stages")
    parser.add_argument("--prefix", default="Bench")
    args = parser.parse_args()
    started = time.perf_counter()
    castle.generate("Warmup", True, True, True)  # loads the tables
    print(f"first call (loads the data): {time.perf_counter() - started:.2f}s")
    for mode in args.modes.split(","):
        rooms, stages = MODES[mode]
        times = []
        attempts = []
        for i in range(args.count):
            started = time.perf_counter()
            layout = castle.generate(f"{args.prefix}{i}", shuffle_rooms=rooms, shuffle_stages=stages)
            times.append(time.perf_counter() - started)
            attempts.append(sum(layout.stage_attempts.values()))
        print(f"{mode:6} {args.count} seeds: median {statistics.median(times):.2f}s, max {max(times):.2f}s, "
              f"mean {statistics.mean(times):.2f}s; stage shuffles per seed median {statistics.median(attempts)}, "
              f"max {max(attempts)}")


if __name__ == "__main__":
    main()
