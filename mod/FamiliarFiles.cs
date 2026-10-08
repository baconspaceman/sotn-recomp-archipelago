#nullable enable
using System;
using System.Collections.Generic;
using RecompOne.Runtime.Memory;

namespace SotnArchipelago;

// The familiars' overlays (TT_000..TT_006: bat, ghost, faerie, demon, sword, sprite, nose devil), loaded
// to 0x80170000 when one is summoned. A shuffled castle moves the rooms their events happen in, so it
// patches their event tables. The seed's bytes go in while that familiar's overlay is loaded (once the CD
// is idle, so the whole file is in), and its own bytes come back for a save from another seed.
static class FamiliarFiles
{
    const uint Base = 0x80170000;
    // Each overlay's first word (its servant table's first function), which tells them apart.
    static readonly uint[] HeaderWords =
        [0x80171ED4, 0x80171624, 0x801746E8, 0x801744CC, 0x80173E38, 0x801746F4, 0x801742A0];

    static readonly Dictionary<uint, byte>[] _writes = [.. Array.ConvertAll(HeaderWords, _ => new Dictionary<uint, byte>())];
    // The overlays' own bytes, everywhere a seed's went in (the first time, so they're the disc's).
    static readonly Dictionary<uint, byte>[] _original = [.. Array.ConvertAll(HeaderWords, _ => new Dictionary<uint, byte>())];

    // A payload key's familiar (TT_00n), or -1.
    public static int Index(string key) =>
        key.Length == 6 && key.StartsWith("TT_00") && key[5] >= '0' && key[5] < '0' + HeaderWords.Length ? key[5] - '0' : -1;

    public static void Add(int familiar, int offset, byte value) => _writes[familiar][Base + (uint)offset] = value;

    public static void Reset()
    {
        foreach (var writes in _writes) writes.Clear();
    }

    // Game thread, every frame.
    public static void Sync(IMemory m)
    {
        int familiar = Array.IndexOf(HeaderWords, m.ReadU32(Base));
        if (familiar < 0 || !SeedPlan.CdIdle(m)) return;
        bool want = SeedPlan.ActiveFor(m);
        var (writes, original) = (_writes[familiar], _original[familiar]);
        if (want)
            foreach (var (addr, value) in writes)
            {
                byte now = m.ReadU8(addr);
                if (now == value) continue;
                original.TryAdd(addr, now);
                m.WriteU8(addr, value);
            }
        // Anything else a seed changed goes back: all of it for a save from another seed.
        foreach (var (addr, disc) in original)
            if ((!want || !writes.ContainsKey(addr)) && m.ReadU8(addr) != disc) m.WriteU8(addr, disc);
    }
}
