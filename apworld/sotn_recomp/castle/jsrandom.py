"""seedrandom 3.0.5 (David Bau, MIT): the ARC4-based generator SOTN-Shuffler seeds from strings.

Only the string-seed path is ported: seedrandom("name") in JS and SeedRandom("name") here give the
same numbers. The shuffler draws everything as Math.floor(rng() * n), kept as floor_mul.
"""
from typing import List, MutableSequence, TypeVar

_WIDTH = 256
_MASK = 255
_START_DENOM = float(2 ** 48)
_SIGNIFICANCE = float(2 ** 52)
_OVERFLOW = float(2 ** 53)

T = TypeVar("T")


def _mix_key(seed: str) -> List[int]:
    """seedrandom's mixkey: folds the seed's UTF-16 code units into a key of up to 256 bytes."""
    key: List[int] = []
    smear = 0
    units = seed.encode("utf-16-le")
    for j in range(len(units) // 2):
        code = units[2 * j] | (units[2 * j + 1] << 8)
        slot = j & _MASK
        # key[slot] is undefined (NaN in JS, so 0 once ToInt32 applies) until the first lap fills it
        previous = key[slot] if slot < len(key) else 0
        smear = _int32(smear ^ _int32(previous * 19))
        value = _MASK & (smear + code)
        if slot < len(key):
            key[slot] = value
        else:
            key.append(value)
    return key


def _int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


class SeedRandom:
    """rng() returns a float in [0, 1), the same sequence as seedrandom(seed) in JS."""

    __slots__ = ("_s", "_i", "_j")

    def __init__(self, seed: str):
        key = _mix_key(seed) or [0]
        s = list(range(_WIDTH))
        j = 0
        for i in range(_WIDTH):
            t = s[i]
            j = _MASK & (j + key[i % len(key)] + t)
            s[i] = s[j]
            s[j] = t
        self._s = s
        self._i = 0
        self._j = 0
        self._g(_WIDTH)  # RC4-drop[256]

    def _g(self, count: int) -> int:
        s = self._s
        i = self._i
        j = self._j
        r = 0
        for _ in range(count):
            i = (i + 1) & 255
            t = s[i]
            j = (j + t) & 255
            s[i] = s[j]
            s[j] = t
            r = r * 256 + s[(s[i] + t) & 255]
        self._i = i
        self._j = j
        return r

    def __call__(self) -> float:
        # arc4.g(6) and the usual single g(1) top-up, unrolled: this runs for every random draw
        s = self._s
        i = self._i
        j = self._j
        i = (i + 1) & 255
        t = s[i]
        j = (j + t) & 255
        u = s[j]
        s[i] = u
        s[j] = t
        b0 = s[(u + t) & 255]
        i = (i + 1) & 255
        t = s[i]
        j = (j + t) & 255
        u = s[j]
        s[i] = u
        s[j] = t
        b1 = s[(u + t) & 255]
        i = (i + 1) & 255
        t = s[i]
        j = (j + t) & 255
        u = s[j]
        s[i] = u
        s[j] = t
        b2 = s[(u + t) & 255]
        i = (i + 1) & 255
        t = s[i]
        j = (j + t) & 255
        u = s[j]
        s[i] = u
        s[j] = t
        b3 = s[(u + t) & 255]
        i = (i + 1) & 255
        t = s[i]
        j = (j + t) & 255
        u = s[j]
        s[i] = u
        s[j] = t
        b4 = s[(u + t) & 255]
        i = (i + 1) & 255
        t = s[i]
        j = (j + t) & 255
        u = s[j]
        s[i] = u
        s[j] = t
        b5 = s[(u + t) & 255]
        i = (i + 1) & 255
        t = s[i]
        j = (j + t) & 255
        u = s[j]
        s[i] = u
        s[j] = t
        x = s[(u + t) & 255]
        self._i = i
        self._j = j
        n = float((((((b0 << 8) | b1) << 8 | b2) << 8 | b3) << 8 | b4) << 8 | b5)
        if n >= _SIGNIFICANCE / _WIDTH:
            # the common case: one round of the JS loop, using the 7th byte drawn above
            n = n * _WIDTH
            d = _START_DENOM * _WIDTH
        else:
            # rare: the JS loop needs more bytes; replay it from the 6-byte numerator
            n = (n + 0) * _WIDTH
            d = _START_DENOM * _WIDTH
            while n < _SIGNIFICANCE:
                n = (n + x) * _WIDTH
                d *= _WIDTH
                x = self._g(1)
        while n >= _OVERFLOW:
            n /= 2
            d /= 2
            x >>= 1
        return (n + x) / d

    def floor_mul(self, n: int) -> int:
        """Math.floor(rng() * n)"""
        return int(self() * n)


def shuffle_in_place(rng: SeedRandom, array: MutableSequence[T]) -> MutableSequence[T]:
    """common.js shuffleArray: Fisher-Yates from the end."""
    for i in range(len(array) - 1, 0, -1):
        j = int(rng() * (i + 1))
        array[i], array[j] = array[j], array[i]
    return array
