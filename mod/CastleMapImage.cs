#nullable enable
using System.Collections.Generic;
using RecompOne.Runtime.Memory;
using Sotn;

namespace SotnArchipelago;

// The pause map's picture: F_MAP.BIN, a 256x256 4bpp image that main() loads to 0x801E0000 at boot and the
// game reads for the whole session (each square is copied into the map page in VRAM as it's explored). A
// seed with a shuffled castle carries its own picture as patch bytes. They go in once the game has booted
// and come out again for a save from another seed; when that happens mid-game the map page is redrawn.
//
// The game itself changes six cells of the picture on every redraw (repaintMapTilesOnCastleBlueprint: the
// Underground Caverns ferry routes, blank in the first castle and filled in in the inverted one). With the
// seed's picture in, that's done at the seed's cells (CastleHooks), so those cells' bytes belong to the
// repaint: they're put in with the picture but not checked against it afterwards. Cells the vanilla repaint
// changed before the seed's picture went in are put back as the disc has them.
static class CastleMapImage
{
    public const uint Base = 0x801E0000;
    const uint RedrawMap = 0x800F2120;   // repaints the ferry cells, clears the map page, redraws every explored square
    const int CheckEveryFrames = 30;

    static readonly List<(uint Addr, byte Value)> _bytes = [];
    static readonly Dictionary<uint, byte> _original = [];
    static readonly HashSet<uint> _repainted = [];   // the seed's ferry cells' bytes
    static bool _applied;
    static int _appliedFor;                           // SeedCastle.LayoutId the bytes went in with

    // Whether the seed's picture is in RAM.
    public static bool Active => _applied;
    public static bool HasPicture => _bytes.Count > 0;
    public static IReadOnlyList<(uint Addr, byte Value)> Bytes => _bytes;

    public static void Add(int offset, byte value) => _bytes.Add((Base + (uint)offset, value));

    // A new seed: the old one's bytes come out first.
    public static void Reset(IMemory? m)
    {
        if (m != null) Restore(m);
        _bytes.Clear();
    }

    // Game thread, every frame.
    public static void Sync(IMemory m, long frame)
    {
        SeedCastle.Update();
        if (frame % CheckEveryFrames != 0) return;
        if (_bytes.Count == 0 && _original.Count == 0) return;
        var state = (GameState)m.ReadU8(Game.GameStateAddr);
        if (state is GameState.Init or GameState.Boot) return; // F_MAP isn't in yet

        bool changed = _bytes.Count > 0 && SeedPlan.ActiveFor(m) ? Apply(m) : Restore(m);
        if (changed && state == GameState.Play) GameApi.Call(RedrawMap);
    }

    // The seed's picture into RAM, unless it's all there already (the repainted cells aside).
    static bool Apply(IMemory m)
    {
        int layout = SeedCastle.LayoutId;
        if (_applied && _appliedFor == layout && Intact(m)) return false;

        _repainted.Clear();
        if (CastleHooks.SeedFerryCells is { } seedCells)
            foreach (var (x, y) in seedCells)
                foreach (var offset in CellBytes(x, y)) _repainted.Add(Base + (uint)offset);

        var patched = new HashSet<uint>();
        foreach (var (addr, value) in _bytes)
        {
            patched.Add(addr);
            _original.TryAdd(addr, m.ReadU8(addr));
            m.WriteU8(addr, value);
        }
        // Whatever the vanilla repaint left in its cells goes; the seed's cells are the repaint's to change.
        foreach (var (x, y) in CastleHooks.VanillaFerryCells)
            foreach (var offset in CellBytes(x, y))
            {
                uint addr = Base + (uint)offset;
                if (patched.Contains(addr) || SeedCastle.VanillaMapByte(offset) is not { } disc) continue;
                _original.TryAdd(addr, m.ReadU8(addr));
                m.WriteU8(addr, disc);
            }
        foreach (var addr in _repainted) _original.TryAdd(addr, m.ReadU8(addr));

        _applied = true;
        _appliedFor = layout;
        return true;
    }

    static bool Intact(IMemory m)
    {
        foreach (var (addr, value) in _bytes)
            if (!_repainted.Contains(addr) && m.ReadU8(addr) != value) return false;
        return true;
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
        _repainted.Clear();
        _applied = false;
        return changed;
    }

    // The F_MAP bytes a repaint of map cell (x, y) changes: the top four rows of its 5x5 block, 4 bits a pixel.
    static IEnumerable<int> CellBytes(int x, int y)
    {
        for (int row = 0; row < 4; row++)
            for (int b = 0; b < 3; b++)
                yield return (y * SeedCastle.CellPixels + row) * SeedCastle.MapRowBytes + x * 2 + b;
    }
}
