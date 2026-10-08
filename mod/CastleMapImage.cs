#nullable enable
using System.Collections.Generic;
using RecompOne.Runtime.Context;
using RecompOne.Runtime.Memory;
using RecompOne.Runtime.Modding;
using Sotn;

namespace SotnArchipelago;

// The pause map's picture: F_MAP.BIN, a 256x256 4bpp image that main() loads to 0x801E0000 at boot and the
// game reads for the whole session (each square is copied into the map page in VRAM as it's explored). A
// seed with a shuffled castle carries its own picture as patch bytes. They go in once the game has booted
// and come out again for a save from another seed; when that happens mid-game the map page is redrawn.
static class CastleMapImage
{
    const uint Base = 0x801E0000;
    const uint RedrawMap = 0x800F2120;   // clears the map page and redraws every explored square
    const int CheckEveryFrames = 30;

    static readonly List<(uint Addr, byte Value)> _bytes = [];
    static readonly Dictionary<uint, byte> _original = [];
    static bool _applied;

    public static bool Active => _applied;

    public static void Add(int offset, byte value) => _bytes.Add((Base + (uint)offset, value));

    // A new seed: the old one's bytes come out first.
    public static void Reset(IMemory? m)
    {
        if (m != null) Restore(m);
        _bytes.Clear();
    }

    public static void Sync(IMemory m, long frame)
    {
        if (frame % CheckEveryFrames != 0) return;
        if (_bytes.Count == 0 && _original.Count == 0) return;
        var state = (GameState)m.ReadU8(Game.GameStateAddr);
        if (state is GameState.Init or GameState.Boot) return; // F_MAP isn't in yet

        bool changed = false;
        if (_bytes.Count > 0 && SeedPlan.ActiveFor(m))
        {
            foreach (var (addr, value) in _bytes)
            {
                byte now = m.ReadU8(addr);
                if (now == value) continue;
                _original.TryAdd(addr, now);
                m.WriteU8(addr, value);
                changed = true;
            }
            _applied = true;
        }
        else changed = Restore(m);

        if (changed && state == GameState.Play) GameApi.Call(RedrawMap);
    }

    static bool Restore(IMemory m)
    {
        bool changed = false;
        foreach (var (addr, value) in _original)
        {
            if (m.ReadU8(addr) == value) continue;
            m.WriteU8(addr, value);
            changed = true;
        }
        _original.Clear();
        _applied = false;
        return changed;
    }

    // On every stage load the game blanks six squares of the Underground Caverns ferry routes in the picture
    // (first castle) or paints them (inverted castle), at their vanilla positions. With a shuffled castle
    // those squares belong to other rooms, so it's skipped. TODO(map rando): redo it at the seed's positions.
    [PreHook("dra", "repaintMapTilesOnCastleBlueprint")]
    static bool BeforeRepaintMapTiles(CpuContext c, IMemory m) => !_applied;
}
