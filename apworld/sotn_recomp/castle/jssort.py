"""Array.prototype.sort as V8 does it (TimSort, third_party/v8/builtins/array-sort.tq).

SOTN-Shuffler sorts room edges with a comparator that is not a consistent order (an edge without a
name compares equal to everything in its room), so the result depends on the exact steps of V8's
sort. This is a step-for-step port; for a consistent comparator it gives the same result as any
stable sort.
"""
from typing import Any, Callable, List

Compare = Callable[[Any, Any], int]

_MIN_GALLOP_WINS = 7


def js_sort(items: List[Any], compare: Compare) -> List[Any]:
    """Sorts items in place the way V8 does with the given comparator; returns items."""
    _TimSort(items, compare).sort()
    return items


class _TimSort:
    def __init__(self, work: List[Any], compare: Compare):
        self.work = work
        self.compare = compare
        self.min_gallop = _MIN_GALLOP_WINS
        self.runs: List[List[int]] = []  # [base, length]

    def sort(self) -> None:
        length = len(self.work)
        if length < 2:
            return
        remaining = length
        low = 0
        min_run = _min_run_length(remaining)
        while remaining != 0:
            run = self._count_and_make_run(low, low + remaining)
            if run < min_run:
                forced = min(min_run, remaining)
                self._binary_insertion_sort(low, low + run, low + forced)
                run = forced
            self.runs.append([low, run])
            self._merge_collapse()
            low += run
            remaining -= run
        self._merge_force_collapse()

    def _binary_insertion_sort(self, low: int, start: int, high: int) -> None:
        work = self.work
        if start == low:
            start += 1
        while start < high:
            left = low
            right = start
            pivot = work[start]
            while left < right:
                mid = left + ((right - left) >> 1)
                if self.compare(pivot, work[mid]) < 0:
                    right = mid
                else:
                    left = mid + 1
            work[left + 1:start + 1] = work[left:start]
            work[left] = pivot
            start += 1

    def _count_and_make_run(self, low_arg: int, high: int) -> int:
        work = self.work
        low = low_arg + 1
        if low == high:
            return 1
        run = 2
        descending = self.compare(work[low], work[low - 1]) < 0
        previous = work[low]
        for idx in range(low + 1, high):
            current = work[idx]
            order = self.compare(current, previous)
            if descending:
                if order >= 0:
                    break
            elif order < 0:
                break
            previous = current
            run += 1
        if descending:
            work[low_arg:low_arg + run] = work[low_arg:low_arg + run][::-1]
        return run

    def _invariant(self, n: int) -> bool:
        if n < 2:
            return True
        runs = self.runs
        return runs[n - 2][1] > runs[n - 1][1] + runs[n][1]

    def _merge_collapse(self) -> None:
        runs = self.runs
        while len(runs) > 1:
            n = len(runs) - 2
            if not self._invariant(n + 1) or not self._invariant(n):
                if runs[n - 1][1] < runs[n + 1][1]:
                    n -= 1
                self._merge_at(n)
            elif runs[n][1] <= runs[n + 1][1]:
                self._merge_at(n)
            else:
                break

    def _merge_force_collapse(self) -> None:
        runs = self.runs
        while len(runs) > 1:
            n = len(runs) - 2
            if n > 0 and runs[n - 1][1] < runs[n + 1][1]:
                n -= 1
            self._merge_at(n)

    def _merge_at(self, i: int) -> None:
        runs = self.runs
        work = self.work
        base_a, length_a = runs[i]
        base_b, length_b = runs[i + 1]
        runs[i][1] = length_a + length_b
        del runs[i + 1]
        k = self._gallop_right(work, work[base_b], base_a, length_a, 0)
        base_a += k
        length_a -= k
        if length_a == 0:
            return
        length_b = self._gallop_left(work, work[base_a + length_a - 1], base_b, length_b, length_b - 1)
        if length_b == 0:
            return
        if length_a <= length_b:
            self._merge_low(base_a, length_a, base_b, length_b)
        else:
            self._merge_high(base_a, length_a, base_b, length_b)

    def _gallop_left(self, array: List[Any], key: Any, base: int, length: int, hint: int) -> int:
        compare = self.compare
        last_ofs = 0
        offset = 1
        if compare(array[base + hint], key) < 0:
            max_ofs = length - hint
            while offset < max_ofs:
                if compare(array[base + hint + offset], key) >= 0:
                    break
                last_ofs = offset
                offset = (offset << 1) + 1
            if offset > max_ofs:
                offset = max_ofs
            last_ofs += hint
            offset += hint
        else:
            max_ofs = hint + 1
            while offset < max_ofs:
                if compare(array[base + hint - offset], key) < 0:
                    break
                last_ofs = offset
                offset = (offset << 1) + 1
            if offset > max_ofs:
                offset = max_ofs
            last_ofs, offset = hint - offset, hint - last_ofs
        last_ofs += 1
        while last_ofs < offset:
            m = last_ofs + ((offset - last_ofs) >> 1)
            if compare(array[base + m], key) < 0:
                last_ofs = m + 1
            else:
                offset = m
        return offset

    def _gallop_right(self, array: List[Any], key: Any, base: int, length: int, hint: int) -> int:
        compare = self.compare
        last_ofs = 0
        offset = 1
        if compare(key, array[base + hint]) < 0:
            max_ofs = hint + 1
            while offset < max_ofs:
                if compare(key, array[base + hint - offset]) >= 0:
                    break
                last_ofs = offset
                offset = (offset << 1) + 1
            if offset > max_ofs:
                offset = max_ofs
            last_ofs, offset = hint - offset, hint - last_ofs
        else:
            max_ofs = length - hint
            while offset < max_ofs:
                if compare(key, array[base + hint + offset]) < 0:
                    break
                last_ofs = offset
                offset = (offset << 1) + 1
            if offset > max_ofs:
                offset = max_ofs
            last_ofs += hint
            offset += hint
        last_ofs += 1
        while last_ofs < offset:
            m = last_ofs + ((offset - last_ofs) >> 1)
            if compare(key, array[base + m]) < 0:
                offset = m
            else:
                last_ofs = m + 1
        return offset

    def _merge_low(self, base_a: int, length_a: int, base_b: int, length_b: int) -> None:
        work = self.work
        compare = self.compare
        temp = work[base_a:base_a + length_a]
        dest = base_a
        cursor_temp = 0
        cursor_b = base_b
        work[dest] = work[cursor_b]
        dest += 1
        cursor_b += 1
        length_b -= 1
        outcome = None  # "succeed" or "copy_b"
        if length_b == 0:
            outcome = "succeed"
        elif length_a == 1:
            outcome = "copy_b"
        min_gallop = self.min_gallop
        while outcome is None:
            wins_a = 0
            wins_b = 0
            while True:
                if compare(work[cursor_b], temp[cursor_temp]) < 0:
                    work[dest] = work[cursor_b]
                    dest += 1
                    cursor_b += 1
                    wins_b += 1
                    length_b -= 1
                    wins_a = 0
                    if length_b == 0:
                        outcome = "succeed"
                        break
                    if wins_b >= min_gallop:
                        break
                else:
                    work[dest] = temp[cursor_temp]
                    dest += 1
                    cursor_temp += 1
                    wins_a += 1
                    length_a -= 1
                    wins_b = 0
                    if length_a == 1:
                        outcome = "copy_b"
                        break
                    if wins_a >= min_gallop:
                        break
            if outcome is not None:
                break
            min_gallop += 1
            first = True
            while wins_a >= _MIN_GALLOP_WINS or wins_b >= _MIN_GALLOP_WINS or first:
                first = False
                min_gallop = max(1, min_gallop - 1)
                self.min_gallop = min_gallop
                wins_a = self._gallop_right(temp, work[cursor_b], cursor_temp, length_a, 0)
                if wins_a > 0:
                    work[dest:dest + wins_a] = temp[cursor_temp:cursor_temp + wins_a]
                    dest += wins_a
                    cursor_temp += wins_a
                    length_a -= wins_a
                    if length_a == 1:
                        outcome = "copy_b"
                        break
                    if length_a == 0:
                        outcome = "succeed"
                        break
                work[dest] = work[cursor_b]
                dest += 1
                cursor_b += 1
                length_b -= 1
                if length_b == 0:
                    outcome = "succeed"
                    break
                wins_b = self._gallop_left(work, temp[cursor_temp], cursor_b, length_b, 0)
                if wins_b > 0:
                    work[dest:dest + wins_b] = work[cursor_b:cursor_b + wins_b]
                    dest += wins_b
                    cursor_b += wins_b
                    length_b -= wins_b
                    if length_b == 0:
                        outcome = "succeed"
                        break
                work[dest] = temp[cursor_temp]
                dest += 1
                cursor_temp += 1
                length_a -= 1
                if length_a == 1:
                    outcome = "copy_b"
                    break
            if outcome is not None:
                break
            min_gallop += 1
            self.min_gallop = min_gallop
        if outcome == "succeed":
            if length_a > 0:
                work[dest:dest + length_a] = temp[cursor_temp:cursor_temp + length_a]
        else:
            work[dest:dest + length_b] = work[cursor_b:cursor_b + length_b]
            work[dest + length_b] = temp[cursor_temp]

    def _merge_high(self, base_a: int, length_a: int, base_b: int, length_b: int) -> None:
        work = self.work
        compare = self.compare
        temp = work[base_b:base_b + length_b]
        dest = base_b + length_b - 1
        cursor_temp = length_b - 1
        cursor_a = base_a + length_a - 1
        work[dest] = work[cursor_a]
        dest -= 1
        cursor_a -= 1
        length_a -= 1
        outcome = None  # "succeed" or "copy_a"
        if length_a == 0:
            outcome = "succeed"
        elif length_b == 1:
            outcome = "copy_a"
        min_gallop = self.min_gallop
        while outcome is None:
            wins_a = 0
            wins_b = 0
            while True:
                if compare(temp[cursor_temp], work[cursor_a]) < 0:
                    work[dest] = work[cursor_a]
                    dest -= 1
                    cursor_a -= 1
                    wins_a += 1
                    length_a -= 1
                    wins_b = 0
                    if length_a == 0:
                        outcome = "succeed"
                        break
                    if wins_a >= min_gallop:
                        break
                else:
                    work[dest] = temp[cursor_temp]
                    dest -= 1
                    cursor_temp -= 1
                    wins_b += 1
                    length_b -= 1
                    wins_a = 0
                    if length_b == 1:
                        outcome = "copy_a"
                        break
                    if wins_b >= min_gallop:
                        break
            if outcome is not None:
                break
            min_gallop += 1
            first = True
            while wins_a >= _MIN_GALLOP_WINS or wins_b >= _MIN_GALLOP_WINS or first:
                first = False
                min_gallop = max(1, min_gallop - 1)
                self.min_gallop = min_gallop
                k = self._gallop_right(work, temp[cursor_temp], base_a, length_a, length_a - 1)
                wins_a = length_a - k
                if wins_a > 0:
                    dest -= wins_a
                    cursor_a -= wins_a
                    work[dest + 1:dest + 1 + wins_a] = work[cursor_a + 1:cursor_a + 1 + wins_a]
                    length_a -= wins_a
                    if length_a == 0:
                        outcome = "succeed"
                        break
                work[dest] = temp[cursor_temp]
                dest -= 1
                cursor_temp -= 1
                length_b -= 1
                if length_b == 1:
                    outcome = "copy_a"
                    break
                k = self._gallop_left(temp, work[cursor_a], 0, length_b, length_b - 1)
                wins_b = length_b - k
                if wins_b > 0:
                    dest -= wins_b
                    cursor_temp -= wins_b
                    work[dest + 1:dest + 1 + wins_b] = temp[cursor_temp + 1:cursor_temp + 1 + wins_b]
                    length_b -= wins_b
                    if length_b == 1:
                        outcome = "copy_a"
                        break
                    if length_b == 0:
                        outcome = "succeed"
                        break
                work[dest] = work[cursor_a]
                dest -= 1
                cursor_a -= 1
                length_a -= 1
                if length_a == 0:
                    outcome = "succeed"
                    break
            if outcome is not None:
                break
            min_gallop += 1
            self.min_gallop = min_gallop
        if outcome == "succeed":
            if length_b > 0:
                work[dest - (length_b - 1):dest + 1] = temp[0:length_b]
        else:
            dest -= length_a
            cursor_a -= length_a
            work[dest + 1:dest + 1 + length_a] = work[cursor_a + 1:cursor_a + 1 + length_a]
            work[dest] = temp[cursor_temp]


def _min_run_length(n: int) -> int:
    r = 0
    while n >= 64:
        r |= n & 1
        n >>= 1
    return n + r
