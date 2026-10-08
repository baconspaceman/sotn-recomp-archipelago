#nullable enable
using System;
using System.Collections.Generic;
using RecompOne.Runtime.Context;
using RecompOne.Runtime.Memory;
using RecompOne.Runtime.Modding;
using Sotn;

namespace SotnArchipelago;

// The code half of a shuffled castle. Sestren's SOTN Shuffler patches game code as well as data: its BIN-Patcher
// rewrites a few instructions' constants to follow the seed's room positions, and its "normalize" patches change
// a handful of entities so their rooms still work when they're put next to other rooms. The recomp runs compiled
// code, so those instruction bytes do nothing in RAM; each patch is done here instead, as a hook that gives the
// same result as the patched instructions. Every hook leaves the game alone unless the seed shuffles the castle
// (SeedCastle) and applies to the save in play (SeedPlan.ActiveFor).
//
// Where the seed's numbers come from: BIN-Patcher's change-dependencies template (bins/sotn-us/constants.js,
// CHANGE_DEPENDENCIES_TEMPLATE "miscellaneous", and LIVE_MAP_REPAINTS), worked out from the seed's room tables.
static class CastleHooks
{
    static bool Active(IMemory m) => SeedCastle.Shuffled && SeedPlan.ActiveFor(m);

    // ---- the seed's room positions these patches follow ----

    const int StageNO4 = 0x09, StageRNO4 = 0x29, StageRNO0 = 0x20, StageTOP = 0x0B, StageRTOP = 0x2B;

    // Rooms by their index in the stage's room table (BIN-Patcher ALIASES), with their vanilla corner, which is
    // checked so a different disc can't send these hooks to the wrong rooms.
    static readonly (int Stage, int Index, int Left, int Top, string Name)[] SpotRooms =
    [
        (StageNO4, 25, 9, 42, "Underground Caverns, left ferryman route"),
        (StageNO4, 23, 40, 41, "Underground Caverns, right ferryman route"),
        (StageNO4, 37, 45, 33, "Underground Caverns, false save room"),
        (StageRNO4, 37, 18, 30, "Reverse Caverns, false save room"),
        (StageRNO0, 31, 32, 36, "Black Marble Gallery, save room"),
        (StageTOP, 2, 30, 5, "Castle Keep, keep area"),
        (StageRTOP, 2, 26, 51, "Reverse Keep, keep area"),
    ];

    sealed record Spots(
        (int X, int Y)[] Ferry,                  // the six map cells repaintMapTilesOnCastleBlueprint repaints
        (int X, int Y) FalseSave, (int X, int Y) ReverseFalseSave, (int X, int Y) FinalSave,
        int KeepX, int KeepY, int ReverseKeepX, int ReverseKeepY);   // where Up flips the castle, in pixels

    static Spots? _spots;
    static int _spotsFor;
    static bool _spotsFailed;

    static Spots? CurrentSpots()
    {
        int layout = SeedCastle.LayoutId;
        if (layout == 0 || !SeedCastle.Shuffled) return null;
        if (_spotsFor == layout) return _spots;
        _spotsFor = layout;
        _spots = WorkOutSpots();
        return _spots;
    }

    static Spots? WorkOutSpots()
    {
        var rooms = new SeedCastle.Room[SpotRooms.Length];
        for (int i = 0; i < SpotRooms.Length; i++)
        {
            var (stage, index, left, top, name) = SpotRooms[i];
            if (!SeedCastle.TryRoom(stage, index, out var vanilla, out var seed) || vanilla.Left != left || vanilla.Top != top)
            {
                if (!_spotsFailed) Log.Error($"castle: {name} isn't room {index} of stage 0x{stage:X2}; the castle's code patches are off");
                _spotsFailed = true;
                return null;
            }
            rooms[i] = seed;
        }
        var (leftRoute, rightRoute, falseSave, reverseFalseSave, finalSave, keep, reverseKeep) =
            (rooms[0], rooms[1], rooms[2], rooms[3], rooms[4], rooms[5], rooms[6]);
        return new Spots(
            // LIVE_MAP_REPAINTS: a, b in the left route; c, d, e, f in the right one, one row below its top.
            [(leftRoute.Left + 5, leftRoute.Top + 1), (leftRoute.Left + 7, leftRoute.Top + 1),
             (rightRoute.Left + 3, rightRoute.Top + 1), (rightRoute.Left + 4, rightRoute.Top + 1),
             (rightRoute.Left + 5, rightRoute.Top + 1), (rightRoute.Left + 8, rightRoute.Top + 1)],
            // specialSaveRooms: the rooms' own corners (they're one cell each).
            (falseSave.Left, falseSave.Top), (reverseFalseSave.Left, reverseFalseSave.Top), (finalSave.Left, finalSave.Top),
            // castleTeleporters: keepArea.left * 256 + 320 and so on (BIN-Patcher stores their negatives).
            keep.Left * 256 + 320, keep.Top * 256 + 847, reverseKeep.Left * 256 + 1728, reverseKeep.Top * 256 + 1351);
    }

    // The map cells the seed's repaint changes, or null when it isn't known.
    public static (int X, int Y)[]? SeedFerryCells => CurrentSpots()?.Ferry;

    // Vanilla: Underground Caverns, left ferryman route (14,43), (16,43); right ferryman route (43..45,42), (48,42).
    public static readonly (int X, int Y)[] VanillaFerryCells = [(14, 43), (16, 43), (43, 42), (44, 42), (45, 42), (48, 42)];

    // ---- game addresses ----

    const uint StageIdAddr = 0x800974A0;          // g_StageId
    const uint TilemapAddr = 0x80073084;          // g_Tilemap: fg at +0, left +0x2C, top +0x30, y +0x40
    const uint TilemapFg = TilemapAddr, TilemapLeft = TilemapAddr + 0x2C, TilemapTop = TilemapAddr + 0x30;
    const uint TilemapY = TilemapAddr + 0x40;
    const uint PlayerXAddr = 0x800973F0, PlayerYAddr = 0x800973F4;   // g_PlayerX, g_PlayerY
    const uint PlayerAddr = 0x800733D8;           // g_Entities[0]
    const uint PlayerPosXHi = PlayerAddr + 0x02, PlayerVelocityY = PlayerAddr + 0x0C, PlayerSteps = PlayerAddr + 0x2C;
    const uint CastleFlags = Progress.CastleFlagsAddr;

    const uint EntityStep = 0x2C, EntityParams = 0x30, EntityFacingLeft = 0x14, EntityPosXHi = 0x02, EntityPose = 0x50;
    const uint EntityBreakCount = 0x84;           // ext.breakable.breakCount (s32)
    const uint EntitySize = 0xBC;

    static uint Fg(IMemory m, int cell) => m.ReadU32(TilemapFg) + (uint)(cell * 2);

    // A call through the game's dispatch, with $ra as the replaced code had it; registers come back unchanged.
    static uint Call(CpuContext c, IMemory m, uint returnAddress, uint function, uint a0 = 0, uint a1 = 0, uint a2 = 0)
    {
        uint ra = c.RA;
        c.RA = returnAddress;
        uint v0 = GameApi.Call(c, m, function, a0, a1, a2);
        c.RA = ra;
        return v0;
    }

    // ---- DRA repaintMapTilesOnCastleBlueprint: the ferry routes' map cells ----
    // On every map redraw (stage load, map screen) the game blanks six cells of the Underground Caverns ferry
    // routes in the map picture for the first castle and fills them in for the inverted castle, so the explored
    // map has the same outline in both. BIN-Patcher moves the twelve cell constants with the two routes
    // (liveMapRepaints); here the same calls are made at the seed's cells. While the seed's picture is in RAM
    // but its castle isn't known, nothing is repainted rather than the vanilla cells of someone else's picture.
    const uint ResetMapTile = 0x800F18C4, PaintMapTile = 0x800F1954;
    static readonly uint[] PaintColours = [0, 1, 2, 2, 2, 2];

    [PreHook("dra", "repaintMapTilesOnCastleBlueprint")]
    static bool BeforeRepaintMapTiles(CpuContext c, IMemory m)
    {
        if (!CastleMapImage.Active) return true;
        if (SeedFerryCells is not { } cells) return false;
        bool inverted = c.A0 != 0;
        for (int i = 0; i < cells.Length; i++)
        {
            var (x, y) = cells[i];
            if (inverted) GameApi.Call(c, m, PaintMapTile, (uint)x, (uint)y, PaintColours[i]);
            else GameApi.Call(c, m, ResetMapTile, (uint)x, (uint)y);
        }
        return false;
    }

    // ---- DRA func_800F2288: the bought Castle Map ----
    // Buying the Castle Map from the Librarian reveals the squares of a list in DRA (D_800A2C0C). A shuffled
    // castle brings its own, cut to the list's space: a castle more than 38 squares tall (with the map 64
    // wide) loses its top rows. The rest are revealed here as the game does it: the square's revealed bit
    // in g_CastleMap (the inverted castle's half there) and the square drawn on the map page.
    const uint CastleMapAddr = 0x8006BB74, DrawMapSquare = 0x800F1B08;
    const uint InvertedCastleMap = 0x400;

    [PostHook("dra", "func_800F2288")]
    static void RevealWholeCastle(CpuContext c, IMemory m)
    {
        if (!Active(m)) return;
        uint half = (m.ReadU32(StageIdAddr) & 0x20) != 0 ? InvertedCastleMap : 0;
        foreach (var (x, y) in SeedCastle.RevealCells)
        {
            uint addr = CastleMapAddr + half + (uint)((x >> 2) + y * 16);
            byte revealed = (byte)(3 << ((3 - (x & 3)) * 2) & 0xAA);
            byte bits = m.ReadU8(addr);
            if ((bits & revealed) != 0) continue;
            m.WriteU8(addr, (byte)(bits | revealed));
            GameApi.Call(c, m, DrawMapSquare, (uint)x, (uint)y, 1);
        }
    }

    // ---- DRA func_800F24F4: the special save rooms ----
    // Run when a save room is set up. It knows three save rooms by their map cell: the Black Marble Gallery one
    // (records the "final save point" time), the Reverse Caverns false save room (not a save room) and the
    // Underground Caverns false save room (Succubus' room). BIN-Patcher moves the cells with the rooms
    // (specialSaveRooms). Reimplemented from the generated code with the seed's cells.
    //
    // Unlike BIN-Patcher's patch: its final save room X goes into the instruction that also holds the stage
    // number the code compares g_StageId with (the compiler shared 0x20 between the two), so the patched game
    // only records that time if the room keeps its vanilla X. The room's own check is kept here.
    const uint RoomFlags = 0x8003C708, SaveRoomActive = 0x80137598, SaveRoomEvent = 0x8003C730;
    const uint TimeAttackController = 0x800FD4C0, SaveRoomSetup = 0x801042C4, SaveRoomStart = 0x80105428;
    const uint TimeAttackFinalSavePoint = 0x16, TimeAttackSuccubus = 0x09, TimeAttackGet = 0, TimeAttackSet = 1;

    [Replace("dra", "func_800F24F4")]
    static void SpecialSaveRooms(Action<CpuContext, IMemory> orig, CpuContext c, IMemory m)
    {
        if (!Active(m) || CurrentSpots() is not { } spots)
        {
            orig(c, m);
            return;
        }
        int x = (int)m.ReadU32(TilemapLeft) + ((int)m.ReadU32(PlayerXAddr) >> 8);
        int y = (int)m.ReadU32(TilemapTop) + ((int)m.ReadU32(PlayerYAddr) >> 8);
        if ((m.ReadU16(RoomFlags) & 0x20) == 0)
        {
            m.WriteU32(SaveRoomActive, 0);
            return;
        }
        uint stage = m.ReadU32(StageIdAddr);
        if (stage == StageRNO0 && (x, y) == spots.FinalSave
            && Call(c, m, 0x800F257C, TimeAttackController, TimeAttackFinalSavePoint, TimeAttackGet) == 0)
            Call(c, m, 0x800F2590, TimeAttackController, TimeAttackFinalSavePoint, TimeAttackSet);

        uint setup = 0;
        stage = m.ReadU32(StageIdAddr);
        if (stage == StageRNO4 && (x, y) == spots.ReverseFalseSave)
        {
            m.WriteU32(SaveRoomActive, 0);
            return;
        }
        if (stage == StageNO4 && (x, y) == spots.FalseSave)
        {
            if ((short)m.ReadU16(PlayerPosXHi) == 0x80) m.WriteU32(SaveRoomEvent, 1);
            else if (Call(c, m, 0x800F260C, TimeAttackController, TimeAttackSuccubus, TimeAttackGet) != 0)
            {
                m.WriteU32(SaveRoomActive, 0);
                return;
            }
            setup = 1;
        }
        Call(c, m, 0x800F261C, SaveRoomSetup, setup);
        m.WriteU32(SaveRoomActive, 1);
        Call(c, m, 0x800F2630, SaveRoomStart);
    }

    // ---- DRA/RIC GetTeleportToOtherCastle, func_80115C50 / func_8015BB80: the Castle Keep castle flip ----
    // Pressing Up on one spot of the Keep (and the Reverse Keep) takes Alucard or Richter to the other castle;
    // the spot is an absolute castle position, (g_Tilemap.left << 8) + g_PlayerX. BIN-Patcher moves it with the
    // keep area (castleTeleporters). Both functions only read globals and the player's position, so they're
    // reimplemented with the seed's spot. RIC's copy differs in one place, kept here: for the Reverse Keep's
    // height it takes the absolute value before subtracting the spot, so any height up to it passes.
    const uint StandPressingUp = 0x00010000;      // PLAYER.step Player_Stand, step_s Player_Stand_PressUp
    const uint ToReverseKeep = 2, ToKeep = 4;

    static uint Abs(uint v) => (int)v < 0 ? 0 - v : v;

    static bool Near(uint tile, uint pixel, int spot) => (int)Abs((tile << 8) + pixel - (uint)spot) < 4;

    static uint TeleportToOtherCastle(IMemory m, Spots spots, bool richter)
    {
        if (m.ReadU32(PlayerSteps) != StandPressingUp) return 0;
        uint stage = m.ReadU32(StageIdAddr);
        uint left = m.ReadU32(TilemapLeft), top = m.ReadU32(TilemapTop);
        uint x = m.ReadU32(PlayerXAddr), y = m.ReadU32(PlayerYAddr);
        if (stage == StageTOP && Near(left, x, spots.KeepX) && Near(top, y, spots.KeepY))
            return ToReverseKeep;
        if (stage == StageRTOP && Near(left, x, spots.ReverseKeepX)
            && (richter ? (int)(Abs((top << 8) + y) - (uint)spots.ReverseKeepY) < 4 : Near(top, y, spots.ReverseKeepY)))
            return ToKeep;
        return 0;
    }

    static bool InKeep(IMemory m) => m.ReadU32(StageIdAddr) is StageTOP or StageRTOP;

    [Replace("dra", "GetTeleportToOtherCastle_dra")]
    static void AlucardTeleportCheck(Action<CpuContext, IMemory> orig, CpuContext c, IMemory m)
    {
        if (!InKeep(m) || !Active(m) || CurrentSpots() is not { } spots) orig(c, m);
        else c.V0 = TeleportToOtherCastle(m, spots, richter: false);
    }

    [Replace("ric", "GetTeleportToOtherCastle_ric")]
    static void RichterTeleportCheck(Action<CpuContext, IMemory> orig, CpuContext c, IMemory m)
    {
        if (!InKeep(m) || !Active(m) || CurrentSpots() is not { } spots) orig(c, m);
        else c.V0 = TeleportToOtherCastle(m, spots, richter: true);
    }

    // While the flip plays, the player is walked a pixel a frame onto the spot's exact X.
    static void WalkOntoTeleportSpot(IMemory m, Spots spots)
    {
        uint stage = m.ReadU32(StageIdAddr);
        int spot = stage == StageTOP ? spots.KeepX : stage == StageRTOP ? spots.ReverseKeepX : 0;
        if (spot == 0) return;
        if ((int)(AbsoluteX(m) - (uint)spot) > 0) m.WriteU16(PlayerPosXHi, (ushort)(m.ReadU16(PlayerPosXHi) - 1));
        if ((int)(AbsoluteX(m) - (uint)spot) < 0) m.WriteU16(PlayerPosXHi, (ushort)(m.ReadU16(PlayerPosXHi) + 1));
    }

    // abs((g_Tilemap.left << 8) + g_PlayerX), as the game works it out (g_PlayerX isn't PLAYER.posX).
    static uint AbsoluteX(IMemory m) => Abs((m.ReadU32(TilemapLeft) << 8) + m.ReadU32(PlayerXAddr));

    [Replace("dra", "func_80115C50")]
    static void AlucardTeleportWalk(Action<CpuContext, IMemory> orig, CpuContext c, IMemory m)
    {
        if (!InKeep(m) || !Active(m) || CurrentSpots() is not { } spots) orig(c, m);
        else WalkOntoTeleportSpot(m, spots);
    }

    [Replace("ric", "func_8015BB80")]
    static void RichterTeleportWalk(Action<CpuContext, IMemory> orig, CpuContext c, IMemory m)
    {
        if (!InKeep(m) || !Active(m) || CurrentSpots() is not { } spots) orig(c, m);
        else WalkOntoTeleportSpot(m, spots);
    }

    // ---- foreground tiles written somewhere else ----
    // Several normalize patches move the block of foreground tiles an entity writes (a breakable floor or wall,
    // a door, a trapdoor). Before the call the tiles it is about to write are noted; after it, they're put back
    // as they were and the patched code's tiles are written instead (read from the same tile tables in RAM,
    // which carry the seed's data patches).
    static readonly List<(uint Addr, ushort Value)> _undo = [];
    static readonly List<(uint Addr, ushort Value)> _redo = [];

    static void Undo(IMemory m, int cell)
    {
        uint addr = Fg(m, cell);
        _undo.Add((addr, m.ReadU16(addr)));
    }

    static void Redo(IMemory m, int cell, uint data) => _redo.Add((Fg(m, cell), m.ReadU16(data)));

    static void ClearTiles()
    {
        _undo.Clear();
        _redo.Clear();
    }

    static void FinishTiles(IMemory m)
    {
        foreach (var (addr, value) in _undo) m.WriteU16(addr, value);
        foreach (var (addr, value) in _redo) m.WriteU16(addr, value);
        ClearTiles();
    }

    // A breakable floor/wall: step 0 writes the intact (or, with its castle flag set, broken) tiles, step 2 the
    // next stage of breaking. A block is `Rows` rows `RowStep` cells apart, `Row` its cells relative to the row's
    // first; its tile table holds `Phase` bytes per stage of breaking (the broken one `Opened` bytes in) and
    // `RowBytes` per row, a row's tiles one after another. `Hits`: what each hit adds to the count, which
    // breaks it at 3.
    sealed record Breakable(uint Flag, uint Table, int Phase, int Opened, int RowBytes, int VanillaCorner,
                            int SeedCorner, int[] Row, int RowStep, int Rows, int Hits);

    static void BeforeBreakable(CpuContext c, IMemory m, Breakable b)
    {
        ClearTiles();
        uint self = c.A0;
        uint step = m.ReadU16(self + EntityStep);
        if (step is not (0 or 2) || !Active(m)) return;  // the step first: this runs every frame
        uint data;
        if (step == 0) data = b.Table + (uint)(m.ReadU8(b.Flag) != 0 ? b.Opened : 0);
        else
        {
            int count = (int)m.ReadU32(self + EntityBreakCount);
            // The patched code counts `Hits` a hit instead of 1: start the game's own +1 that much further on.
            if (b.Hits > 1) m.WriteU32(self + EntityBreakCount, (uint)(count + b.Hits - 1));
            data = b.Table + (uint)((count + b.Hits) * b.Phase);
        }
        for (int r = 0; r < b.Rows; r++)
            for (int i = 0; i < b.Row.Length; i++)
            {
                Undo(m, b.VanillaCorner + r * b.RowStep + b.Row[i]);
                Redo(m, b.SeedCorner + r * b.RowStep + b.Row[i], data + (uint)(r * b.RowBytes + 2 * i));
            }
    }

    // NO4 EntityBreakableCrystalFloor (Underground Caverns' crystal floor): tile block 0x2D3 -> 0x2C6, and it
    // breaks in one hit (count += 3 instead of 1).
    static readonly Breakable CrystalFloor = new(CastleFlags + 0xC4, 0x80181880, 12, 0x24, 6, 0x2D3, 0x2C6, [0, 1, 2], 0x10, 2, 3);
    // RNO4 func_801C96AC (the Reverse Caverns' one, upside down): tile block 0x2C -> 0x39.
    static readonly Breakable ReverseCrystalFloor = new(CastleFlags + 0xCA, 0x801816BC, 12, 0x24, 6, 0x2C, 0x39, [0, -1, -2], -0x10, 2, 1);
    // RNO4 func_801C99A0 (Reverse Caverns' breakable wall): tile column 0x9F -> 0x9E.
    static readonly Breakable ReverseCavernsWall = new(CastleFlags + 0xCB, 0x801816EC, 16, 0x30, 4, 0x9F, 0x9E, [0], -0x10, 4, 1);
    // NZ0 EntityBottomSecretRoomFloor (Alchemy Laboratory's secret floor): 0x2E7 -> 0x2C7, breaks in one hit.
    static readonly Breakable LabFloor = new(CastleFlags + 0x82, 0x80180E94, 8, 0x18, 4, 0x2E7, 0x2C7, [0, 1], 0x10, 2, 3);
    // RNZ0 EntityBottomSecretRoomFloor (Necromancy Laboratory's, upside down): 0x18 -> 0x38.
    static readonly Breakable ReverseLabFloor = new(CastleFlags + 0x89, 0x80180CA8, 8, 0x18, 4, 0x18, 0x38, [0, -1], -0x10, 2, 1);

    [PreHook("no4", "EntityBreakableCrystalFloor")] static void CrystalFloorIn(CpuContext c, IMemory m) => BeforeBreakable(c, m, CrystalFloor);
    [PostHook("no4", "EntityBreakableCrystalFloor")] static void CrystalFloorOut(CpuContext c, IMemory m) => FinishTiles(m);
    [PreHook("rno4", "func_801C96AC")] static void ReverseCrystalFloorIn(CpuContext c, IMemory m) => BeforeBreakable(c, m, ReverseCrystalFloor);
    [PostHook("rno4", "func_801C96AC")] static void ReverseCrystalFloorOut(CpuContext c, IMemory m) => FinishTiles(m);
    [PreHook("rno4", "func_801C99A0")] static void ReverseCavernsWallIn(CpuContext c, IMemory m) => BeforeBreakable(c, m, ReverseCavernsWall);
    [PostHook("rno4", "func_801C99A0")] static void ReverseCavernsWallOut(CpuContext c, IMemory m) => FinishTiles(m);
    [PreHook("nz0", "EntityBottomSecretRoomFloor_nz0")] static void LabFloorIn(CpuContext c, IMemory m) => BeforeBreakable(c, m, LabFloor);
    [PostHook("nz0", "EntityBottomSecretRoomFloor_nz0")] static void LabFloorOut(CpuContext c, IMemory m) => FinishTiles(m);
    [PreHook("rnz0", "EntityBottomSecretRoomFloor_rnz0")] static void ReverseLabFloorIn(CpuContext c, IMemory m) => BeforeBreakable(c, m, ReverseLabFloor);
    [PostHook("rnz0", "EntityBottomSecretRoomFloor_rnz0")] static void ReverseLabFloorOut(CpuContext c, IMemory m) => FinishTiles(m);

    // NO3/NP3 EntityJewelSwordDoor (Castle Entrance's Jewel Sword room wall): when it opens (step 2) the patched
    // code writes its foreground tiles from one row higher (0x3F0 instead of 0x420) and six rows a column
    // instead of five, reading the wall's tile table (secretWallTiles, also patched) six at a time. The
    // background tiles are unchanged.
    const int JewelDoorCorner = 0x420, JewelDoorSeedCorner = 0x3F0, JewelDoorRowStep = 0x30;

    static void BeforeJewelSwordDoor(CpuContext c, IMemory m, uint table)
    {
        ClearTiles();
        if (m.ReadU16(c.A0 + EntityStep) != 2 || !Active(m)) return;
        for (int column = 0; column < 3; column++)
        {
            for (int row = 0; row < 5; row++) Undo(m, JewelDoorCorner + column + row * JewelDoorRowStep);
            for (int row = 0; row < 6; row++)
                Redo(m, JewelDoorSeedCorner + column + row * JewelDoorRowStep, table + (uint)(2 * (6 * column + row)));
        }
    }

    [PreHook("no3", "EntityJewelSwordDoor_no3")] static void JewelSwordDoorIn(CpuContext c, IMemory m) => BeforeJewelSwordDoor(c, m, 0x801812E2);
    [PostHook("no3", "EntityJewelSwordDoor_no3")] static void JewelSwordDoorOut(CpuContext c, IMemory m) => FinishTiles(m);
    [PreHook("np3", "EntityJewelSwordDoor_np3")] static void JewelSwordDoorRevisitedIn(CpuContext c, IMemory m) => BeforeJewelSwordDoor(c, m, 0x801811AA);
    [PostHook("np3", "EntityJewelSwordDoor_np3")] static void JewelSwordDoorRevisitedOut(CpuContext c, IMemory m) => FinishTiles(m);

    // NO0 func_us_801C2E7C (Marble Gallery's trapdoors down to the Underground Caverns): while the caverns are
    // shut, step 0 fills the trapdoor's tile rows with 0x597. The patch fills one row instead of three; the
    // other two keep the room's own tiles.
    const uint TrapdoorTable = 0x80180FF0, CavernsOpenFlag = CastleFlags + 0x01;

    [PreHook("no0", "func_us_801C2E7C")]
    static void TrapdoorIn(CpuContext c, IMemory m)
    {
        ClearTiles();
        if (m.ReadU16(c.A0 + EntityStep) != 0 || m.ReadU8(CavernsOpenFlag) != 0 || !Active(m)) return;
        uint entry = TrapdoorTable + m.ReadU16(c.A0 + EntityParams) * 4u;
        ushort start = m.ReadU16(entry), step = m.ReadU16(entry + 2);
        for (int row = 1; row < 3; row++)
        {
            int cell = (short)(start + row * step);
            for (int i = 0; i < 4; i++) Undo(m, cell + i);
        }
    }

    [PostHook("no0", "func_us_801C2E7C")] static void TrapdoorOut(CpuContext c, IMemory m) => FinishTiles(m);

    // ---- NO3 EntityDeathCutsceneManager: Death's cutscene at the Castle Entrance ----
    // Mottzilla's "Prevent softlocks when meeting Death": step 0 sets g_Tilemap.y to 0xFC (the camera's view of
    // the vanilla room); the patch drops that and stores 0 at -0x4674 from $s1 instead, which in the stage's
    // update loop is the entity itself. The cutscene script always spawns this entity in slot 96 (0x80077A58),
    // so the store is PLAYER.velocityY (0x800733E4). Done here as that: g_Tilemap.y kept, PLAYER.velocityY 0.
    static uint _deathCameraY;
    static bool _deathStep0;

    [PreHook("no3", "EntityDeathCutsceneManager")]
    static void DeathCutsceneIn(CpuContext c, IMemory m)
    {
        _deathStep0 = m.ReadU16(c.A0 + EntityStep) == 0 && Active(m);
        if (_deathStep0) _deathCameraY = m.ReadU32(TilemapY);
    }

    [PostHook("no3", "EntityDeathCutsceneManager")]
    static void DeathCutsceneOut(CpuContext c, IMemory m)
    {
        if (!_deathStep0) return;
        _deathStep0 = false;
        m.WriteU32(TilemapY, _deathCameraY);
        m.WriteU32(PlayerVelocityY, 0);
    }

    // ---- NO4 EntityFerrymanController: the ferry gate ----
    // In case 4 (rowing), on the ferryman's fifth pose, the boat that needs the Merman Statue (params 0) checks
    // its x in the room: vanilla opens the gate (castle flag 0xC2, FERRYMAN_GATE_OPEN) once it passes 0xAA0
    // heading right (facingLeft set) and stops past 0xBE0, and stops below 0x120 heading left. The patch
    // (normalize-underground-caverns, the same for every seed) also opens the gate heading left, anywhere from
    // 0x120 up to 0xC1F, so it opens whichever end the ride starts from; heading right it opens it only between
    // 0xAA1 and 0xBE0 (vanilla also on the frame it stops, if it hadn't yet). Those checks come right after the
    // ferryman's AnimateEntity call in case 4, where the pose and the boat's x are already what they'll test.
    const uint FerryAnimateReturn = 0x801C5FDC;   // $ra of case 4's AnimateEntity(anim_row, ferryman)
    const uint FerryGateFlag = CastleFlags + 0xC2;
    const uint ScrollXHi = 0x8007308E;            // g_Tilemap.scrollX.i.hi
    static bool _ferryGateClosed;

    [PreHook("no4", "EntityFerrymanController")]
    static void FerrymanIn(CpuContext c, IMemory m) => _ferryGateClosed = false;

    [PostHook("no4", "AnimateEntity_no4")]
    static void AfterFerrymanRows(CpuContext c, IMemory m)
    {
        if (c.RA != FerryAnimateReturn || !Active(m)) return;
        uint boat = c.S0, ferryman = c.S1;          // the controller's own registers, kept by the callee
        if (ferryman != boat + EntitySize) return;
        if (m.ReadU16(boat + EntityParams) != 0 || m.ReadU16(ferryman + EntityPose) != 5) return;
        int x = (short)(m.ReadU16(boat + EntityPosXHi) + m.ReadU16(ScrollXHi));
        bool open = m.ReadU8(FerryGateFlag) != 0;
        if (m.ReadU16(boat + EntityFacingLeft) != 0)
            _ferryGateClosed = x >= 0xBE1 && !open;  // vanilla opens it this frame, the patch doesn't
        else if (x >= 0x120 && x < 0xC20)
            m.WriteU8(FerryGateFlag, 1);
    }

    [PostHook("no4", "EntityFerrymanController")]
    static void FerrymanOut(CpuContext c, IMemory m)
    {
        if (!_ferryGateClosed) return;
        _ferryGateClosed = false;
        m.WriteU8(FerryGateFlag, 0);
    }

    // ---- NZ0 NZ0_EntityCutscene: Maria in the Alchemy Laboratory ----
    // The patch makes step 0 always take the "already met" branch, so the cutscene never plays (the recomp's
    // own fix only skips it when Alucard comes in from the left). The branch reads castle flag 0x85, which is
    // reported as set for the call and put back after: the entity goes, the flag stays as it was.
    const uint MetMariaInLab = CastleFlags + 0x85;
    static bool _labCutsceneSpoofed;

    [PreHook("nz0", "NZ0_EntityCutscene")]
    static void LabCutsceneIn(CpuContext c, IMemory m)
    {
        _labCutsceneSpoofed = false;
        if (m.ReadU16(c.A0 + EntityStep) != 0 || m.ReadU8(MetMariaInLab) != 0 || !Active(m)) return;
        m.WriteU8(MetMariaInLab, 1);
        _labCutsceneSpoofed = true;
    }

    [PostHook("nz0", "NZ0_EntityCutscene")]
    static void LabCutsceneOut(CpuContext c, IMemory m)
    {
        if (_labCutsceneSpoofed && m.ReadU8(MetMariaInLab) == 1) m.WriteU8(MetMariaInLab, 0);
        _labCutsceneSpoofed = false;
    }
}
