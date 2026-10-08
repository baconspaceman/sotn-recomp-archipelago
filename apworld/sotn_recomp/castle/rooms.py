"""Node groups: pieces of a stage's map (rooms, a grid of cells and the open edges where doors are),
and how SOTN-Shuffler joins them (shuffle-rooms.js combineNodeGroups, shuffleRooms,
getVanillaNodeGroupForStage).

Edge coordinates in the JS are half-integers (a door on the left of cell (2, 3) is at row 2.5,
column 3); here they are doubled so they stay integers. combine() follows the JS step for step,
including two quirks that change results:
  - edges taken over from the added group lose their edgeName, and the JS sort that orders edges by
    (roomName, edgeName) treats a missing name as equal to anything (jssort reproduces V8's sort);
  - the second edge loop compares `.collisions` (a typo), so only the first loop can reject a
    placement for mismatched edges. Every matched pair is seen by the first loop anyway.
"""
from typing import Dict, List, Optional, Sequence, Tuple

from .data import load
from .jsrandom import SeedRandom, shuffle_in_place
from .jssort import js_sort

# Room: (stage, room, row, column)
Room = Tuple[str, str, int, int]
# Edge: (roomName, edgeName or None, collision, row * 2, column * 2)
Edge = Tuple[str, Optional[str], str, int, int]

MAX_MAP_ROW = 56
MAX_MAP_COL = 63


# '.' -> '0', anything else -> '1', for turning a row of cells into a bit mask
_FILLED_BITS = bytes(48 if c == ord(".") else 49 for c in range(256))


def _row_mask(row: str) -> int:
    return int(row.encode("latin-1").translate(_FILLED_BITS)[::-1] or b"0", 2)


class Group:
    """A node group. The cell grid is kept as one bit mask per row (bit c set where column c is filled),
    and the grid's width: in the JS the cells are strings of room letters, but nothing reads them except
    to tell '.' (empty) from the rest, and the width is that of the first row."""
    __slots__ = ("rooms", "masks", "width", "edges", "_edges_at")

    def __init__(self, rooms: List[Room], masks: List[int], width: int, edges: List[Edge]):
        self.rooms = rooms
        self.masks = masks
        self.width = width
        self.edges = edges
        self._edges_at: Optional[Dict[Tuple[int, int], List[Edge]]] = None

    @property
    def cells(self) -> List[str]:
        """The grid as rows of '#' (filled) and '.', for looking at."""
        return ["".join("#" if (mask >> c) & 1 else "." for c in range(self.width)) for mask in self.masks]

    @property
    def edges_at(self) -> Dict[Tuple[int, int], List[Edge]]:
        """Edges by (row * 2, column * 2) in the group's own coordinates."""
        if self._edges_at is None:
            edges_at: Dict[Tuple[int, int], List[Edge]] = {}
            for e in self.edges:
                edges_at.setdefault((e[3], e[4]), []).append(e)
            self._edges_at = edges_at
        return self._edges_at


def _group_from_json(info) -> Group:
    rooms = [(r["stage"], r["room"], r["row"], r["column"]) for r in info["rooms"]]
    edges = []
    for e in info["edges"]:
        row2 = round(e["row"] * 2)
        col2 = round(e["column"] * 2)
        if (row2 % 2 == 0) == (col2 % 2 == 0):
            raise ValueError(f"edge {e} must have exactly one half-integer coordinate")
        edges.append((e["roomName"], e["edgeName"], e["collision"], row2, col2))
    # Rows may differ in length (colosseum.arena); the masks keep what each row has
    return Group(rooms, [_row_mask(row) for row in info["cells"]], len(info["cells"][0]), edges)


_GROUPS: Dict[bool, Dict[str, Dict[str, Group]]] = {}


def node_groups(normalized: bool) -> Dict[str, Dict[str, Group]]:
    """NODE_GROUPS by stage and group name. normalized: with COLLISIONS applied, as shuffleRooms does
    (to the shared table, so everything after the first room shuffle sees the normalized edges)."""
    if normalized not in _GROUPS:
        shuffler = load("shuffler")
        collisions = shuffler["COLLISIONS"] if normalized else {}
        result: Dict[str, Dict[str, Group]] = {}
        for stage_name, groups in shuffler["NODE_GROUPS"].items():
            result[stage_name] = {}
            for group_name, info in groups.items():
                group = _group_from_json(info)
                for key, collision in collisions.items():
                    stage_key, group_key, edge_name = key.split(".")
                    if stage_key == stage_name and group_key == group_name:
                        index = [e[1] for e in group.edges].index(edge_name)
                        e = group.edges[index]
                        group.edges[index] = (e[0], e[1], collision, e[3], e[4])
                result[stage_name][group_name] = group
        _GROUPS[normalized] = result
    return _GROUPS[normalized]


def _js_less(a: Optional[str], b: Optional[str]) -> bool:
    # JS a < b: false both ways when either side is undefined
    return a is not None and b is not None and a < b


def _edge_compare(a: Edge, b: Edge) -> int:
    # The JS comparator: by roomName, then edgeName
    if _js_less(a[0], b[0]):
        return -1
    if _js_less(b[0], a[0]):
        return 1
    if _js_less(a[1], b[1]):
        return -1
    if _js_less(b[1], a[1]):
        return 1
    return 0


def _filled(masks: List[int], row: int, col: int, width: int) -> bool:
    # A cell counts if it lies in the group, inside the joined grid's width, and isn't '.'
    return 0 <= row < len(masks) and 0 <= col < width and (masks[row] >> col) & 1 == 1


def _check(base: Group, node: Group, row_offset: int, col_offset: int,
           allow_overlaps: bool, allow_mismatched_edges: bool):
    """Everything combineNodeGroups checks; (rows, columns, unsorted edges) or None if it returns null."""
    base_masks = base.masks
    node_masks = node.masks
    base_height = len(base_masks)
    node_height = len(node_masks)
    if row_offset >= 0:
        rows = max(base_height, node_height + row_offset)
        base_row, node_row = 0, row_offset
    else:
        rows = max(base_height, node_height + row_offset) - row_offset
        base_row, node_row = -row_offset, 0
    if col_offset >= 0:
        cols = max(base.width, node.width + col_offset)
        base_col, node_col = 0, col_offset
    else:
        cols = max(base.width, node.width + col_offset) - col_offset
        base_col, node_col = -col_offset, 0
    if rows >= MAX_MAP_ROW or cols >= MAX_MAP_COL:
        return None
    base_width = cols - base_col  # base columns that land inside the joined grid
    node_width = cols - node_col
    if not allow_overlaps:
        base_limit = (1 << base_width) - 1
        node_limit = (1 << node_width) - 1
        for r in range(max(0, -row_offset), min(node_height, base_height - row_offset)):
            b = base_masks[r + row_offset] & base_limit
            n = node_masks[r] & node_limit
            if col_offset >= 0:
                if (b >> col_offset) & n:
                    return None
            elif (b << -col_offset) & n:
                return None

    # Edges meet where a base edge and a node edge have the same position in the joined grid
    node_at = node.edges_at
    base_at = base.edges_at
    row2, col2 = 2 * row_offset, 2 * col_offset
    edges: List[Edge] = []
    valid = True
    for e in base.edges:
        matches = node_at.get((e[3] - row2, e[4] - col2))
        if matches:
            if not allow_mismatched_edges and any(m[2] != e[2] for m in matches):
                valid = False
        else:
            edges.append((e[0], e[1], e[2], e[3] + 2 * base_row, e[4] + 2 * base_col))
    if not valid:
        return None
    for e in node.edges:
        if (e[3] + row2, e[4] + col2) not in base_at:
            edges.append((e[0], None, e[2], e[3] + 2 * node_row, e[4] + 2 * node_col))

    # An open edge may not face a filled cell on both sides
    for _, _, _, row2, col2 in edges:
        if row2 % 2 == 0:
            row_a, row_b = row2 // 2 - 1, row2 // 2
            col_a = col_b = (col2 - 1) // 2
        else:
            row_a = row_b = (row2 - 1) // 2
            col_a, col_b = col2 // 2 - 1, col2 // 2
        if 0 <= row_a < rows and 0 <= row_b < rows and 0 <= col_a < cols and 0 <= col_b < cols:
            if ((_filled(base_masks, row_a - base_row, col_a - base_col, base_width)
                 or _filled(node_masks, row_a - node_row, col_a - node_col, node_width))
                    and (_filled(base_masks, row_b - base_row, col_b - base_col, base_width)
                         or _filled(node_masks, row_b - node_row, col_b - node_col, node_width))):
                return None
    return rows, cols, edges


def fits(base: Group, node: Group, row_offset: int, col_offset: int) -> bool:
    """True if combine(base, node, ...) with no options would not return None."""
    return _check(base, node, row_offset, col_offset, False, False) is not None


def combine(base: Group, node: Group, row_offset: int, col_offset: int,
            allow_overlaps: bool = False, allow_mismatched_edges: bool = False, checked=None) -> Optional[Group]:
    """combineNodeGroups: node placed at (row_offset, col_offset) relative to base. `checked` is the
    result of _check() for the same arguments when the caller already has it."""
    if checked is None:
        checked = _check(base, node, row_offset, col_offset, allow_overlaps, allow_mismatched_edges)
    if checked is None:
        return None
    rows, cols, edges = checked
    base_row, base_col = max(0, -row_offset), max(0, -col_offset)
    node_row, node_col = max(0, row_offset), max(0, col_offset)
    # Each group's rows, cut to the joined grid's width and shifted into place; a cell is filled if
    # either group fills it (where both do, which only allow_overlaps lets through, the JS keeps the
    # base's letter: filled either way)
    base_limit = (1 << (cols - base_col)) - 1
    node_limit = (1 << (cols - node_col)) - 1
    base_masks = base.masks
    node_masks = node.masks
    masks = []
    for r in range(rows):
        row_a = r - base_row
        row_b = r - node_row
        mask = 0
        if 0 <= row_a < len(base_masks):
            mask = (base_masks[row_a] & base_limit) << base_col
        if 0 <= row_b < len(node_masks):
            mask |= (node_masks[row_b] & node_limit) << node_col
        masks.append(mask)
    rooms = [(s, n, r + base_row, c + base_col) for s, n, r, c in base.rooms]
    rooms += [(s, n, r + node_row, c + node_col) for s, n, r, c in node.rooms]
    rooms.sort(key=lambda room: (room[0], room[1]))  # a consistent comparator: any stable sort agrees
    js_sort(edges, _edge_compare)
    return Group(rooms, masks, cols, edges)


def shuffle_rooms(seed: str, stage_name: str) -> Group:
    """shuffleRooms(seed, stageName, true): joins the stage's node groups at random until every edge is
    matched. Retries with the same generator until it succeeds."""
    stage_groups: Sequence[Group] = list(node_groups(True)[stage_name].values())
    rng = SeedRandom(seed)
    while True:
        valid = True
        indexes = list(range(len(stage_groups)))
        shuffle_in_place(rng, indexes)
        result = stage_groups[indexes.pop()]
        while indexes:
            if not result.edges:
                valid = False
                break
            base_edge = result.edges[rng.floor_mul(len(result.edges))]
            placements = []
            for index in indexes:
                for edge in stage_groups[index].edges:
                    row2 = base_edge[3] - edge[3]
                    col2 = base_edge[4] - edge[4]
                    # An odd (half-integer) offset pairs a horizontal edge with a vertical one
                    if row2 % 2 == 0 and col2 % 2 == 0:
                        placements.append((index, row2 // 2, col2 // 2))
            if not placements:
                valid = False
                break
            shuffle_in_place(rng, placements)
            valid = False
            for index, row_offset, col_offset in placements:
                group = stage_groups[index]
                checked = _check(result, group, row_offset, col_offset, False, False)
                if checked is not None:
                    indexes.remove(index)
                    result = combine(result, group, row_offset, col_offset, checked=checked)
                    valid = True
                    break
            if not valid:
                break
        if indexes or result.edges:
            valid = False
        if valid:
            return result


def vanilla_group(stage_name: str, normalized: bool) -> Group:
    """getVanillaNodeGroupForStage: the stage's node groups where the vanilla room table puts them."""
    rooms_fact = load("facts")["arrays"][f"stages.{stage_name}.rooms"]
    aliases = rooms_fact["aliases"]
    stage_top = 63
    stage_left = 63
    for index in aliases.values():
        stage_top = min(stage_top, rooms_fact["top"][index])
        stage_left = min(stage_left, rooms_fact["left"][index])
    result = Group([], [0], 1, [])  # cells: ["."]
    for group_name, group in node_groups(normalized)[stage_name].items():
        index = aliases[group_name]
        own = next(room for room in group.rooms if room[1] == group_name)
        row_offset = rooms_fact["top"][index] - stage_top - own[2]
        col_offset = rooms_fact["left"][index] - stage_left - own[3]
        combined = combine(result, group, row_offset, col_offset, allow_mismatched_edges=True)
        if combined is None:
            raise ValueError(f"vanilla {stage_name} does not fit together at {group_name}")
        result = combined
    return result
