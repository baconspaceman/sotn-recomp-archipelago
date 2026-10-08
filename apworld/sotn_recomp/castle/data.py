"""Loads the castle shuffler's JSON tables from data/castle (made by tools/castle/dump_data.mjs).

Works from a folder or from the zipped .apworld: inside the world the files are read through the
parent package; when this package is imported on its own (the tools), straight from disk.
"""
import functools
import json
import os
import pkgutil
from typing import Any, Dict

_PARENT = __package__.rpartition(".")[0] if __package__ else ""


def _read(name: str) -> bytes:
    if _PARENT:
        data = pkgutil.get_data(_PARENT, f"data/castle/{name}")
        if data is None:
            raise FileNotFoundError(name)
        return data
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data", "castle", name)
    with open(path, "rb") as f:
        return f.read()


@functools.lru_cache(maxsize=None)
def load(name: str) -> Dict[str, Any]:
    """shuffler, logic, patcher, facts or fixed"""
    return json.loads(_read(f"{name}.json"))
