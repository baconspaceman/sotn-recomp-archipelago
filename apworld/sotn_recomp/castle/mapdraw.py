"""The castle map picture (F_MAP.BIN) and the map-purchase reveal mask, drawn as shuffle.js does after
the layout is settled, with the stage-link labels of shuffle-rooms.js getMapPixels.

Colours: the drawings name what each fill is (a stage, a save room, a red door, ...). With area
colours that becomes SOTN-Shuffler's palette index per stage (COLORS), shown with the
customize-map-colors palette. Without, every room uses the game's own map fill index, save rooms and
warp rooms keep their usual red and orange, and the stock palette stays, so the map colour option
still recolours the whole map. How the game uses the indices (dra func_800F1B08): an explored square
copies the picture, with 0 becoming the border colour (14) and 2 becoming 1; a square revealed by the
bought map shows 13 for 0 and 3 for anything else.
"""
from typing import Callable, Dict, List, Sequence

from .data import load
from .rooms import Room

GRID = 256

# Palette indices in the game's map palette (BIN-Patcher's castleMapColorPalettes aliases)
EXPLORED_FILL = "1"
SAVE_ROOM_FILL = "4"
WARP_ROOM_FILL = "5"
EMPTY = "0"  # drawn as the border colour once explored


def colour_scheme(area_colors: bool) -> Callable[[str], str]:
    """Maps a colour name used in the drawings (a COLORS key) to a palette index character."""
    colors = load("shuffler")["COLORS"]
    if area_colors:
        return lambda name: colors[name]

    def single(name: str) -> str:
        if name in ("loadingRoom", "obstacle", "wall"):
            return EMPTY
        if name == "saveRoom":
            return SAVE_ROOM_FILL
        if name == "warpRooms":
            return WARP_ROOM_FILL
        # every stage, and red doors (the stock map doesn't mark them)
        return EXPLORED_FILL
    return single


def _cap(name: str) -> str:
    return name[:1].upper() + name[1:]


def map_pixels(stage_links: Dict[str, str], labels: bool) -> Dict[str, Dict[str, List[tuple]]]:
    """MAP_PIXELS, plus getMapPixels' labels: each pair of linked loading rooms gets the same letter, drawn
    in the colour of the stage on the other side (pairs with the warp rooms get none).

    Commands per room: ("fill", colour, top, left, rows, columns) and ("glyph", colour, glyph, top, left),
    in pixels from the room's top left, colours as COLORS keys."""
    shuffler = load("shuffler")
    result = {stage: {room: [("fill",) + tuple(cmd) for cmd in cmds] for room, cmds in rooms.items()}
              for stage, rooms in shuffler["MAP_PIXELS"].items()}
    if not labels:
        return result
    teleporters = shuffler["TELEPORTERS"]
    chars = "CDHIJKLNSTUVXYZ147+-####"
    assigned: Dict[str, str] = {}
    for source, target in stage_links.items():
        if "warpRooms" in (teleporters[source]["sourceStage"], teleporters[target]["sourceStage"]):
            continue
        glyph = chars[len(assigned) // 2]
        for this, other in ((source, target), (target, source)):
            if this in assigned:
                continue
            stage = teleporters[this]["sourceStage"]
            room = "loadingRoomTo" + _cap(teleporters[this]["targetStage"])
            joined_stage = teleporters[other]["sourceStage"]
            result[stage][room].append(("fill", "loadingRoom", 1, 1, 3, 3))
            result[stage][room].append(("glyph", joined_stage, glyph, 1, 1))
            assigned[this] = glyph
    return result


def draw(rooms: Sequence[Room], pixels, area_colors: bool, min_row: int, min_col: int) -> List[str]:
    """The 256x256 picture as rows of palette index characters ('0'-'f')."""
    glyphs = load("shuffler")["GLYPHS"]
    scheme = colour_scheme(area_colors)
    grid = ["0" * GRID for _ in range(GRID)]
    for stage, room, row, col in rooms:
        commands = pixels.get(stage, {}).get(room)
        if commands is None:
            continue
        for command in commands:
            if command[0] == "fill":
                _, colour, top, left, rows, columns = command
                index = scheme(colour)
                pixel_row = 4 * (min_row + row) + top
                pixel_col = 4 * (min_col + col) + left
                for r in range(rows):
                    text = grid[pixel_row + r]
                    grid[pixel_row + r] = text[:pixel_col] + index * columns + text[pixel_col + columns:]
            else:
                _, colour, glyph_name, top, left = command
                index = scheme(colour)
                for r, glyph_row in enumerate(glyphs[glyph_name]):
                    for c, ch in enumerate(glyph_row):
                        if ch == ".":
                            continue
                        pixel_row = 4 * (min_row + row) + r + top
                        pixel_col = 4 * (min_col + col) + c + left
                        text = grid[pixel_row]
                        grid[pixel_row] = text[:pixel_col] + index + text[pixel_col + 1:]
    return grid


def reveals(grid: List[str]) -> dict:
    """The map squares the bought Castle Map reveals: every square with something drawn in it, packed
    into the space the game has for it (DRA D_800A2C0C)."""
    cells = set()
    min_row = min_col = 63
    max_row = max_col = 0
    for row, text in enumerate(grid):
        for col, ch in enumerate(text):
            if ch != "0":
                r, c = row // 4, col // 4
                cells.add((r, c))
                min_row = min(min_row, r)
                min_col = min(min_col, c)
                max_row = max(max_row, r)
                max_col = max(max_col, c)
    bytes_per_row = -(-(1 + max_col - min_col) // 8)
    rows = min(64, 1 + max_row - min_row, 2432 // (8 * bytes_per_row))
    result = {
        "bytesPerRow": bytes_per_row,
        "grid": [],
        "left": max(0, 1 + max_col - bytes_per_row * 8),
        "rows": rows,
        "top": max(0, 1 + max_row - rows),
    }
    grid_rows = [[" "] * (8 * bytes_per_row) for _ in range(rows)]
    for r, c in cells:
        row = r - result["top"]
        col = c - result["left"]
        if 0 <= row < rows and 0 <= col < 8 * bytes_per_row:
            grid_rows[row][col] = "#"
    result["grid"] = ["".join(row) for row in grid_rows]
    return result
