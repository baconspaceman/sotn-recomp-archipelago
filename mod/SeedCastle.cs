#nullable enable
using System;
using System.Collections.Generic;
using System.Threading;
using RecompOne.Runtime.Cdrom;
using RecompOne.Runtime.Host;

namespace SotnArchipelago;

// The castle as this seed lays it out. A seed with a shuffled castle moves rooms (each stage's room table)
// and carries its own pause-map picture (F_MAP); both are worked out here as the vanilla files from the disc
// with the seed's patch bytes on top, so they're there in and out of the game. A location keeps its place
// inside its room, so a vanilla map cell becomes the same spot of the room's new position.
//
// Worked out on the game thread once the seed's placement is ready (Update, from CastleMapImage.Sync), and
// published as one unchanging snapshot: the game thread's hooks (CastleHooks) and the map window on the UI
// thread both read whichever snapshot is current.
static class SeedCastle
{
    public const int MapSize = 256;               // F_MAP: 256x256 pixels, 4 bits each; a cell is the 5x5
    public const int CellPixels = 4, BlockPixels = 5;   // block at (4x, 4y), sharing its edges
    public const int MapRowBytes = MapSize / 2;
    const string MapFile = "F_MAP.BIN";
    const string PaletteFile = "BIN/F_GAME.BIN";
    const int PaletteOffset = 0x41800;            // F_GAME: the map's 16 colours
    const int PaletteBytes = 32;
    const int FillIndex = 14;                     // the game fills an explored cell's empty pixels with this
    const uint OverlayBase = 0x80180000;
    const int RoomsPointer = 0x10;                // overlay header: the room table, 8 bytes a room
    const int RoomBytes = 8;
    const byte RoomsEnd = 0x40;
    const int SectorBytes = 2048;

    public readonly record struct Room(int Left, int Top, int Right, int Bottom);

    // Everything worked out for one connection's seed. Never changed once published.
    sealed class Layout
    {
        public int Connection, Id;
        public Dictionary<int, (Room[] Vanilla, Room[] Seed)> Rooms = [];
        public bool Moved;                        // some room is somewhere else than in the vanilla castle
        public byte[]? VanillaMap;                // F_MAP as on the disc
        public byte[]? Rgba, RgbaRotated;         // the seed's picture, as stored and turned round
        public (int X0, int Y0, int X1, int Y1) Drawn = (0, 0, MapSize, MapSize);
        public uint FillColour = 0xFF808080;
    }

    static Layout? _layout;                        // written on the game thread, read on any
    static int _builds;

    static Layout? Current =>
        Volatile.Read(ref _layout) is { } layout && layout.Connection == ApClient.ConnectionId ? layout : null;

    // The seed moves rooms around and has its own map picture.
    public static bool Shuffled => Current is { Moved: true, Rgba: not null };

    // Changes whenever another snapshot becomes current (0: none), for callers that cache what they work out.
    public static int LayoutId => Current?.Id ?? 0;

    // Explored cells' fill colour (ImGui ABGR), as the game draws them.
    public static uint FillColour => Current?.FillColour ?? 0xFF808080;

    // Game thread, every frame: work the seed's castle out once its placement is ready.
    public static void Update()
    {
        int connection = ApClient.ConnectionId;    // first: a connection made after this isn't the one built for
        if (!SeedPlan.Ready || Volatile.Read(ref _layout)?.Connection == connection) return;
        Volatile.Write(ref _layout, Build(connection));
    }

    // A room of a stage's room table (its index in the table), where it is in vanilla and in this seed.
    public static bool TryRoom(int stage, int index, out Room vanilla, out Room seed)
    {
        vanilla = seed = default;
        if (Current is not { } layout || !layout.Rooms.TryGetValue(stage, out var tables)) return false;
        if (index < 0 || index >= tables.Vanilla.Length || index >= tables.Seed.Length) return false;
        (vanilla, seed) = (tables.Vanilla[index], tables.Seed[index]);
        return true;
    }

    // Where a vanilla map cell of this stage is in the seed's castle, or null when no room holds it.
    public static (int X, int Y)? Cell(int stage, int x, int y)
    {
        if (Current is not { } layout || !layout.Rooms.TryGetValue(stage, out var tables)) return null;
        var (vanilla, seed) = tables;
        for (int i = 0; i < vanilla.Length && i < seed.Length; i++)
        {
            var r = vanilla[i];
            if (x < r.Left || x > r.Right || y < r.Top || y > r.Bottom) continue;
            return (seed[i].Left + x - r.Left, seed[i].Top + y - r.Top);
        }
        return null;
    }

    // A byte of F_MAP as the disc has it (no seed bytes), or null before the castle is worked out.
    public static byte? VanillaMapByte(int offset) =>
        Current?.VanillaMap is { } map && offset >= 0 && offset < map.Length ? map[offset] : null;

    // UI thread: the map picture as a texture, as stored (first castle) or turned 180 degrees as the game shows
    // it in the inverted castle, lined up so cell (x, y) of the inverted castle's rooms is at pixel (4x, 4y) too.
    static Layout? _texturesFor;
    static uint _texture, _textureRotated;

    public static uint Texture(bool inverted)
    {
        if (Current is not { Rgba: { } rgba, RgbaRotated: { } rotated } layout) return 0;
        if (!ReferenceEquals(_texturesFor, layout))
        {
            _texture = HostWindow.UploadTexture(rgba, MapSize, MapSize);
            _textureRotated = HostWindow.UploadTexture(rotated, MapSize, MapSize);
            _texturesFor = layout;
        }
        return inverted ? _textureRotated : _texture;
    }

    // The part of the picture with anything drawn on it, plus a cell's margin, in pixels: from (X0, Y0) up to
    // (X1, Y1), exclusive. Turned round with the picture for the inverted castle.
    const int BoundsMargin = CellPixels;

    public static (int X0, int Y0, int X1, int Y1) Bounds(bool inverted)
    {
        var (x0, y0, x1, y1) = Current?.Drawn ?? (0, 0, MapSize, MapSize);
        if (inverted) (x0, y0, x1, y1) = (MapSize + 1 - x1, MapSize + 1 - y1, MapSize + 1 - x0, MapSize + 1 - y0);
        return (Math.Max(0, x0 - BoundsMargin), Math.Max(0, y0 - BoundsMargin),
                Math.Min(MapSize, x1 + BoundsMargin), Math.Min(MapSize, y1 + BoundsMargin));
    }

    // ---- working it out (game thread) ----

    static Layout Build(int connection)
    {
        var layout = new Layout { Connection = connection, Id = ++_builds };
        var fs = RecompOne.Runtime.Runtime.Cd?.Fs;
        if (fs == null)
        {
            Log.Error("castle: the disc isn't readable, so the seed's castle can't be worked out");
            return layout;
        }
        try
        {
            foreach (var (key, zone) in SpecialData.Zones)
            {
                if (ReadRooms(fs, DiscPath(key), zone.Stage) is not { } tables) continue;
                layout.Rooms[zone.Stage] = tables;
                for (int i = 0; i < tables.Vanilla.Length && i < tables.Seed.Length; i++)
                    if (tables.Vanilla[i] != tables.Seed[i]) layout.Moved = true;
            }
            if (CastleMapImage.HasPicture) DrawPicture(layout, fs);
            if (layout.Moved) Log.Info("castle: this seed's castle is shuffled");
        }
        catch (Exception ex)
        {
            Log.Error($"castle: couldn't work out the seed's castle: {ex.Message}");
            layout = new Layout { Connection = connection, Id = layout.Id };
        }
        return layout;
    }

    static string DiscPath(string key) =>
        key.StartsWith("BO") || key.StartsWith("RBO") ? $"BOSS/{key}/{key}.BIN" : $"ST/{key}/{key}.BIN";

    // A stage's room table, vanilla and with the seed's writes. Only the parts of the file it needs are read:
    // the header for the table's address, then the table up to its end marker.
    static (Room[] Vanilla, Room[] Seed)? ReadRooms(DiscFs fs, string path, int stage)
    {
        if (!fs.Locate(path, out int lba, out uint size)) return null;
        var writes = SeedPlan.WritesFor(stage);
        var header = ReadPart(fs, lba, size, 0, RoomsPointer + 4);
        long table = BitConverter.ToUInt32(header, RoomsPointer) - OverlayBase;
        var seedHeader = Patched(header, 0, writes);
        long seedTable = BitConverter.ToUInt32(seedHeader, RoomsPointer) - OverlayBase;
        return (ReadTable(fs, lba, size, table, null), ReadTable(fs, lba, size, seedTable, writes));
    }

    static Room[] ReadTable(DiscFs fs, int lba, uint size, long at, IReadOnlyList<RamWrite>? writes)
    {
        var rooms = new List<Room>();
        if (at < 0 || at >= size) return [];
        for (int length = 64 * RoomBytes; ; length *= 2)
        {
            var bytes = ReadPart(fs, lba, size, (int)at, length);
            if (writes != null) bytes = Patched(bytes, at, writes);
            rooms.Clear();
            int i = 0;
            for (; i + RoomBytes <= bytes.Length && bytes[i] != RoomsEnd; i += RoomBytes)
                rooms.Add(new Room(bytes[i], bytes[i + 1], bytes[i + 2], bytes[i + 3]));
            if (i < bytes.Length || at + bytes.Length >= size) return [.. rooms];
        }
    }

    // `length` bytes of a file from `offset` (fewer at the end of the file).
    static byte[] ReadPart(DiscFs fs, int lba, uint size, int offset, int length)
    {
        length = (int)Math.Min(length, size - offset);
        int skip = offset % SectorBytes;
        var sectors = fs.ReadSectors(lba + offset / SectorBytes, skip + length);
        return sectors.AsSpan(skip, length).ToArray();
    }

    // A piece of an overlay file (from file offset `at`) with the seed's RAM writes that fall in it.
    static byte[] Patched(byte[] bytes, long at, IReadOnlyList<RamWrite> writes)
    {
        var copy = (byte[])bytes.Clone();
        foreach (var w in writes)
        {
            long offset = w.Addr - OverlayBase - at;
            for (int b = 0; b < w.Size; b++)
                if (offset + b >= 0 && offset + b < copy.Length) copy[offset + b] = (byte)(w.Value >> (8 * b));
        }
        return copy;
    }

    static void DrawPicture(Layout layout, DiscFs fs)
    {
        var map = fs.ReadFile(MapFile);
        layout.VanillaMap = (byte[])map.Clone();
        foreach (var (addr, value) in CastleMapImage.Bytes)
        {
            long at = addr - CastleMapImage.Base;
            if (at >= 0 && at < map.Length) map[at] = value;
        }

        if (!fs.Locate(PaletteFile, out int lba, out uint size)) throw new InvalidOperationException($"{PaletteFile} not found");
        var paletteBytes = ReadPart(fs, lba, size, PaletteOffset, PaletteBytes);
        if (SeedPlan.GraphicsBytes("F_GAME") is { } chunks)
            foreach (var list in chunks.Values)
                foreach (var (offset, value) in list)
                    if (offset >= PaletteOffset && offset < PaletteOffset + PaletteBytes) paletteBytes[offset - PaletteOffset] = value;

        var palette = new uint[16];
        for (int i = 0; i < 16; i++) palette[i] = Abgr(BitConverter.ToUInt16(paletteBytes, i * 2));
        layout.FillColour = palette[FillIndex] | 0xFF000000;

        var rgba = new byte[MapSize * MapSize * 4];
        var rotated = new byte[MapSize * MapSize * 4];
        int minX = MapSize, minY = MapSize, maxX = -1, maxY = -1;
        for (int y = 0; y < MapSize; y++)
            for (int x = 0; x < MapSize; x++)
            {
                byte b = map[y * MapRowBytes + x / 2];
                uint colour = palette[(x & 1) == 0 ? b & 15 : b >> 4];
                if (colour != 0)
                {
                    minX = Math.Min(minX, x); maxX = Math.Max(maxX, x);
                    minY = Math.Min(minY, y); maxY = Math.Max(maxY, y);
                }
                Put(rgba, x, y, colour);
                // Turned round 256 - p rather than 255 - p, so a cell's block stays at (4x, 4y).
                if (x > 0 && y > 0) Put(rotated, MapSize - x, MapSize - y, colour);
            }
        layout.Drawn = maxX < 0 ? (0, 0, MapSize, MapSize) : (minX, minY, maxX + 1, maxY + 1);
        layout.Rgba = rgba;
        layout.RgbaRotated = rotated;
    }

    static void Put(byte[] rgba, int x, int y, uint abgr)
    {
        int i = (y * MapSize + x) * 4;
        rgba[i] = (byte)abgr;
        rgba[i + 1] = (byte)(abgr >> 8);
        rgba[i + 2] = (byte)(abgr >> 16);
        rgba[i + 3] = (byte)(abgr >> 24);
    }

    // A PlayStation colour as ImGui ABGR; 0x0000 is see-through, as on the PlayStation.
    static uint Abgr(ushort psx)
    {
        if (psx == 0) return 0;
        uint Channel(int shift) => (uint)(((psx >> shift) & 31) * 255 / 31);
        return 0xFF000000 | Channel(10) << 16 | Channel(5) << 8 | Channel(0);
    }
}
