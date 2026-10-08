#nullable enable
using System;
using System.Collections.Generic;
using RecompOne.Runtime.Host;

namespace SotnArchipelago;

// The castle as this seed lays it out, for the map window. A seed with a shuffled castle moves rooms (each
// stage's room table) and carries its own pause-map picture (F_MAP); both are worked out here as the vanilla
// files from the disc with the seed's patch bytes on top, so they're there in and out of the game. A
// location keeps its place inside its room, so a vanilla map cell becomes the same spot of the room's new
// position.
static class SeedCastle
{
    public const int MapSize = 256;               // F_MAP: 256x256 pixels, 4 bits each; a cell is the 5x5
    public const int CellPixels = 4, BlockPixels = 5;   // block at (4x, 4y), sharing its edges
    const int MapRowBytes = MapSize / 2;
    const string MapFile = "F_MAP.BIN";
    const string PaletteFile = "BIN/F_GAME.BIN";
    const int PaletteOffset = 0x41800;            // F_GAME: the map's 16 colours
    const int FillIndex = 14;                     // the game fills an explored cell's empty pixels with this
    const uint OverlayBase = 0x80180000;
    const int RoomsPointer = 0x10;                // overlay header: the room table, 8 bytes a room

    readonly record struct Room(int Left, int Top, int Right, int Bottom);

    static int _builtFor = -1;
    static readonly Dictionary<int, (List<Room> Vanilla, List<Room> Seed)> _rooms = [];
    static byte[]? _rgba, _rgbaRotated;
    static int _texturesFor = -1;
    static uint _texture, _textureRotated;

    // The seed moves rooms around and has its own map picture.
    public static bool Shuffled => SeedPlan.Ready && CastleMapImage.HasPicture;

    // Explored cells' fill colour (ImGui ABGR), as the game draws them.
    public static uint FillColour { get; private set; } = 0xFF808080;

    // Where a vanilla map cell of this stage is in the seed's castle, or null when no room holds it.
    public static (int X, int Y)? Cell(int stage, int x, int y)
    {
        Build();
        if (!_rooms.TryGetValue(stage, out var tables)) return null;
        var (vanilla, seed) = tables;
        for (int i = 0; i < vanilla.Count && i < seed.Count; i++)
        {
            var r = vanilla[i];
            if (x < r.Left || x > r.Right || y < r.Top || y > r.Bottom) continue;
            return (seed[i].Left + x - r.Left, seed[i].Top + y - r.Top);
        }
        return null;
    }

    // The map picture as a texture: as stored (first castle), or turned 180 degrees as the game shows it in
    // the inverted castle, lined up so cell (x, y) of the inverted castle's rooms is at pixel (4x, 4y) too.
    public static uint Texture(bool inverted)
    {
        Build();
        if (_rgba == null || _rgbaRotated == null) return 0;
        if (_texturesFor != _builtFor)
        {
            _texture = HostWindow.UploadTexture(_rgba, MapSize, MapSize);
            _textureRotated = HostWindow.UploadTexture(_rgbaRotated, MapSize, MapSize);
            _texturesFor = _builtFor;
        }
        return inverted ? _textureRotated : _texture;
    }

    static void Build()
    {
        if (!Shuffled || _builtFor == ApClient.ConnectionId) return;
        _builtFor = ApClient.ConnectionId;
        _rooms.Clear();
        _rgba = _rgbaRotated = null;
        var fs = RecompOne.Runtime.Runtime.Cd?.Fs;
        if (fs == null)
        {
            Log.Error("map: the disc isn't readable, so the seed's castle can't be drawn");
            return;
        }
        try
        {
            foreach (var (key, zone) in SpecialData.Zones)
            {
                var file = fs.ReadFile(DiscPath(key));
                var vanilla = ReadRooms(file);
                foreach (var w in SeedPlan.WritesFor(zone.Stage))
                {
                    long at = w.Addr - OverlayBase;
                    if (at < 0 || at + w.Size > file.Length) continue;
                    file[at] = (byte)w.Value;
                    if (w.Size == 2) file[at + 1] = (byte)(w.Value >> 8);
                }
                _rooms[zone.Stage] = (vanilla, ReadRooms(file));
            }
            DrawPicture(fs.ReadFile(MapFile), fs.ReadFile(PaletteFile));
        }
        catch (Exception ex)
        {
            Log.Error($"map: couldn't work out the seed's castle: {ex.Message}");
        }
    }

    static string DiscPath(string key) =>
        key.StartsWith("BO") || key.StartsWith("RBO") ? $"BOSS/{key}/{key}.BIN" : $"ST/{key}/{key}.BIN";

    static List<Room> ReadRooms(byte[] file)
    {
        var rooms = new List<Room>();
        long at = BitConverter.ToUInt32(file, RoomsPointer) - OverlayBase;
        for (; at >= 0 && at + 8 <= file.Length; at += 8)
        {
            if (file[at] == 0x40) break;
            rooms.Add(new Room(file[at], file[at + 1], file[at + 2], file[at + 3]));
        }
        return rooms;
    }

    static void DrawPicture(byte[] map, byte[] game)
    {
        foreach (var (addr, value) in CastleMapImage.Bytes)
        {
            long at = addr - CastleMapImage.Base;
            if (at >= 0 && at < map.Length) map[at] = value;
        }
        if (SeedPlan.GraphicsBytes("F_GAME") is { } chunks)
            foreach (var list in chunks.Values)
                foreach (var (offset, value) in list)
                    if (offset >= PaletteOffset && offset < PaletteOffset + 32) game[offset] = value;

        var palette = new uint[16];
        for (int i = 0; i < 16; i++) palette[i] = Abgr(BitConverter.ToUInt16(game, PaletteOffset + i * 2));
        FillColour = palette[FillIndex] | 0xFF000000;

        _rgba = new byte[MapSize * MapSize * 4];
        _rgbaRotated = new byte[MapSize * MapSize * 4];
        for (int y = 0; y < MapSize; y++)
            for (int x = 0; x < MapSize; x++)
            {
                byte b = map[y * MapRowBytes + x / 2];
                uint colour = palette[(x & 1) == 0 ? b & 15 : b >> 4];
                Put(_rgba, x, y, colour);
                // Turned round 256 - p rather than 255 - p, so a cell's block stays at (4x, 4y).
                if (x > 0 && y > 0) Put(_rgbaRotated, MapSize - x, MapSize - y, colour);
            }
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
