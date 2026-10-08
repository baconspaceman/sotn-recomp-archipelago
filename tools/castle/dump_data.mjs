// Regenerates apworld/sotn_recomp/data/castle/*.json: the castle shuffler's data, dumped from
// SOTN-Shuffler and BIN-Patcher (both by Sestren, MIT) plus small fact tables read from the disc.
//
// usage:
//   node tools/castle/dump_data.mjs --shuffler DIR --disc TRACK1.BIN [--out DIR] [--extraction FILE]
//        [--recomp-config FILE]
//
//   --shuffler       a SOTN-Shuffler checkout at commit efb1db1 with node_modules installed and
//                    BIN-Patcher (commit cf4d991) in lib/BIN-Patcher
//   --disc           the US disc image (SLUS-00067, track 1); only read
//   --out            where the JSON goes (default: apworld/sotn_recomp/data/castle next to this tool)
//   --extraction     reuse an extraction BIN-Patcher made from the same disc instead of making one
//   --recomp-config  SymphonyRecomp's config/sotn.json (default: ref/SymphonyRecomp/config/sotn.json);
//                    its function maps tell code from data
//
// Nothing is written into the checkout. Private tables are read through expose_hooks.mjs, which needs a
// Node with module.registerHooks (22.15 or later; made with 26.1). tools/castle/regen.sh does the
// checkout, this and a parity check in one go.
//
// Output (compact JSON):
//   shuffler.json  NODE_GROUPS, COLLISIONS, COLORS, GLYPHS, MAP_PIXELS (colour names, not indices),
//                  LINKS, STAGE_LINKS, TELEPORTERS, STAGE_NAMES, map offsets
//   logic.json     ROOMS_INFO, ROOM_PRIORITY, VALIDATIONS, LOGIC.locations (the per-stage room checks)
//   patcher.json   BIN-Patcher's tables for working out derived values (getChangeDependencies)
//   facts.json     where things are on the disc: per file key and offset, sizes, aliases, the vanilla
//                  room rectangles and teleporter entries the generator reads
//   fixed.json     the bytes the seed-independent patches change (normalize-*, Power of Wolf id,
//                  map colours) as sparse diffs against the disc, and the code sites they touch

import './expose_hooks.mjs'

import fs from 'fs'
import path from 'path'
import { execFileSync } from 'child_process'
import { fileURLToPath, pathToFileURL } from 'url'

const PINNED = {
    shuffler: 'efb1db1a964bb2000cde2f668d8d9a099165fb32',
    binPatcher: 'cf4d99116e41e6998a65e3d64c8af69ac754c894',
}

// Seed-independent patches whose data parts the castle shuffler uses, in the order patch.sh applies them
const FIXED_PATCHES = {
    powerOfWolf: ['assign-power-of-wolf-relic-a-unique-id.json'],
    mapColors: ['customize-map-colors.json'],
    normalize: [
        'normalize-abandoned-mine.json',
        'normalize-alchemy-laboratory.json',
        'normalize-castle-entrance.json',
        'normalize-clock-tower.json',
        'normalize-long-library.json',
        'normalize-marble-gallery.json',
        'normalize-olroxs-quarters.json',
        'normalize-underground-caverns.json',
    ],
}

function parseArgs(argv) {
    const result = {}
    for (let i = 0; i < argv.length; i++) {
        if (argv[i].startsWith('--')) {
            result[argv[i].slice(2)] = argv[i + 1]
            i++
        }
    }
    return result
}

const here = path.dirname(fileURLToPath(import.meta.url))
const args = parseArgs(process.argv.slice(2))
if (!args.shuffler || !args.disc) {
    console.error('usage: node tools/castle/dump_data.mjs --shuffler DIR --disc TRACK1.BIN [--out DIR] ' +
        '[--extraction FILE] [--recomp-config FILE]')
    process.exit(2)
}
const shufflerDir = path.resolve(args.shuffler)
const bpDir = path.join(shufflerDir, 'lib', 'BIN-Patcher')
const outDir = path.resolve(args.out ?? path.join(here, '..', '..', 'apworld', 'sotn_recomp', 'data', 'castle'))
const recompConfig = path.resolve(
    args['recomp-config'] ?? path.join(here, '..', '..', 'ref', 'SymphonyRecomp', 'config', 'sotn.json'))

// Code is told apart from data by SymphonyRecomp's function maps: file key -> [[start, end)] offsets
function loadFunctionRanges(configPath) {
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'))
    const ranges = {}
    for (const overlay of config.overlays) {
        const key = path.posix.basename(overlay.file).split('.')[0].toUpperCase()
        if (key in ranges) {
            continue
        }
        const base = parseInt(overlay.base, 16)
        const funcMap = JSON.parse(fs.readFileSync(path.join(path.dirname(configPath), overlay.funcMap), 'utf8'))
        ranges[key] = funcMap.functions.map((fn) => {
            const start = parseInt(fn.address, 16) - base
            return [start, start + (fn.size ?? 4), fn.name]
        })
    }
    return ranges
}
const functionRanges = loadFunctionRanges(recompConfig)
const functionAt = (file, offset) => {
    const hit = (functionRanges[file] ?? []).find(([start, end]) => start <= offset && offset < end)
    return hit ? hit[2] : null
}

function gitHead(dir) {
    try {
        return execFileSync('git', ['-C', dir, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
    }
    catch (e) {
        return 'unknown'
    }
}
const commits = { shuffler: gitHead(shufflerDir), binPatcher: gitHead(bpDir) }
for (const [name, want] of Object.entries(PINNED)) {
    if (commits[name] !== want) {
        console.error(`warning: ${name} is at ${commits[name]}, the data was made from ${want}`)
    }
}

const importFrom = (dir, file, query = '') => import(pathToFileURL(path.join(dir, file)).href + query)
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'))

const rooms = await importFrom(shufflerDir, 'src/shuffle-rooms.js')
const roomsSemantic = await importFrom(shufflerDir, 'src/shuffle-rooms.js', '?semantic')
const stages = await importFrom(shufflerDir, 'src/shuffle-stages.js')
const constants = await importFrom(shufflerDir, 'src/constants.js')
const sotnUs = await importFrom(bpDir, 'bins/sotn-us/sotn-us.js')
const bpCommon = await importFrom(bpDir, 'src/common.js')
const bpExtract = await importFrom(bpDir, 'src/extract.js')
const bpChange = await importFrom(bpDir, 'src/change.js')
const bpPpf = await importFrom(bpDir, 'src/ppf.js')

// ---- the disc ----

console.log('reading disc')
const disc = fs.readFileSync(args.disc)
const SECTOR = 2352
const gdByte = (gd) => disc[Math.floor(gd / 2048) * SECTOR + 24 + (gd % 2048)]

// ISO9660 directory: every file's game-data extent, so an address can be named as (file key, offset)
function readIsoFiles() {
    const sector = (lba) => disc.subarray(lba * SECTOR + 24, lba * SECTOR + 24 + 2048)
    const files = []
    const walk = (lba, size, prefix) => {
        const chunks = []
        for (let i = 0; i < Math.ceil(size / 2048); i++) {
            chunks.push(sector(lba + i))
        }
        const data = Buffer.concat(chunks)
        let pos = 0
        while (pos < data.length) {
            const len = data[pos]
            if (len === 0) {
                pos = (Math.floor(pos / 2048) + 1) * 2048
                continue
            }
            const rec = data.subarray(pos, pos + len)
            pos += len
            const extent = rec.readUInt32LE(2)
            const extentSize = rec.readUInt32LE(10)
            const flags = rec[25]
            const nameLength = rec[32]
            const raw = rec.subarray(33, 33 + nameLength)
            if (nameLength === 1 && (raw[0] === 0 || raw[0] === 1)) {
                continue
            }
            const name = prefix + '/' + raw.toString('latin1').split(';')[0]
            if (flags & 2) {
                walk(extent, extentSize, name)
            }
            else {
                files.push({ name, start: extent * 2048, end: extent * 2048 + extentSize })
            }
        }
    }
    const root = sector(16).subarray(156, 156 + 34)
    walk(root.readUInt32LE(2), root.readUInt32LE(10), '')
    return files
}
const isoFiles = readIsoFiles()
function fileOf(gd) {
    const f = isoFiles.find((entry) => entry.start <= gd && gd < entry.end)
    if (!f) {
        throw new Error(`address 0x${gd.toString(16)} is not inside a file`)
    }
    return [path.posix.basename(f.name).split('.')[0].toUpperCase(), gd - f.start]
}

// ---- extraction (BIN-Patcher's reading of this disc) ----

let extraction
if (args.extraction) {
    extraction = JSON.parse(fs.readFileSync(args.extraction, 'utf8'))
}
else {
    console.log('extracting (BIN-Patcher)')
    const cwd = process.cwd()
    process.chdir(bpDir) // processBinary reads its templates from relative paths
    extraction = sotnUs.processBinary(new bpCommon.GameData(disc)).extraction
    process.chdir(cwd)
}
const aliasesJson = JSON.parse(fs.readFileSync(path.join(bpDir, 'bins/sotn-us/data/aliases.json'), 'utf8'))

// BIN-Patcher names the last familiar overlay (TT_006) noseDevil, but aliases.json files its aliases
// under noseDemon, so every write the shuffler makes to it lands nowhere. It is the same table layout
// as every other familiar's; the port supplies the aliases (a deliberate fix, see check_parity.py).
const noseDemon = aliasesJson.familiarEvents.noseDemon
if (extraction.familiarEvents.noseDevil.aliases) {
    throw new Error('familiarEvents.noseDevil has aliases now; the noseDemon fix is no longer needed')
}
if (JSON.stringify(noseDemon) !== JSON.stringify(extraction.familiarEvents.bat.aliases)) {
    throw new Error('noseDemon aliases differ from the other familiars')
}
extraction.familiarEvents.noseDevil.aliases = noseDemon

const toVal = bpCommon.toVal
const nodeAt = (p) => p.split('.').reduce((n, t) => n?.[t], extraction)

// ---- shuffler.json ----

// MAP_PIXELS with colour names: [colourName, top, left, rows, columns] per fillRect
function semanticMapPixels() {
    const result = {}
    for (const [stageName, stageInfo] of Object.entries(roomsSemantic.MAP_PIXELS)) {
        result[stageName] = {}
        for (const [roomName, commands] of Object.entries(stageInfo)) {
            result[stageName][roomName] = commands.map((command) => {
                const p = command.parameters
                if (command.command !== 'fillRect' || !String(p.colorIndex).startsWith('@')) {
                    throw new Error(`unexpected map command in ${stageName}.${roomName}: ${JSON.stringify(command)}`)
                }
                return [p.colorIndex.slice(1), p.top, p.left, p.rows, p.columns]
            })
        }
    }
    // The semantic table must resolve to exactly the real one
    for (const [stageName, stageInfo] of Object.entries(rooms.MAP_PIXELS)) {
        for (const [roomName, commands] of Object.entries(stageInfo)) {
            commands.forEach((command, i) => {
                const [colour, top, left, rows, columns] = result[stageName][roomName][i]
                const p = command.parameters
                if (rooms.__COLORS[colour] !== p.colorIndex || top !== p.top || left !== p.left ||
                    rows !== p.rows || columns !== p.columns) {
                    throw new Error(`semantic MAP_PIXELS differs at ${stageName}.${roomName}[${i}]`)
                }
            })
        }
    }
    return result
}

function shuffleJsConstants() {
    const source = fs.readFileSync(path.join(shufflerDir, 'shuffle.js'), 'utf8')
    const names = source.match(/const STAGE_NAMES = \[([\s\S]*?)\]/)[1]
    const stageNames = [...names.matchAll(/'([A-Za-z]+)'/g)].map((m) => m[1])
    const minCol = Number(source.match(/const MIN_MAP_COL = (\d+)/)[1])
    const minRow = Number(source.match(/const MIN_MAP_ROW = (\d+)/)[1])
    return { stageNames, minCol, minRow }
}

const links = {}
for (const [name, info] of Object.entries(stages.__LINKS)) {
    links[name] = {
        stage: info.stage,
        direction: info.direction,
        forbiddenConnections: Array.from(info.forbiddenConnections),
        minimumTargetLinkCount: info.minimumTargetLinkCount,
    }
}
const jsConstants = shuffleJsConstants()
const shufflerJson = {
    _source: { project: 'SOTN-Shuffler (Sestren, MIT)', commit: commits.shuffler },
    STAGE_NAMES: jsConstants.stageNames,
    MIN_MAP_ROW: jsConstants.minRow,
    MIN_MAP_COL: jsConstants.minCol,
    NODE_GROUPS: rooms.NODE_GROUPS,
    COLLISIONS: rooms.__COLLISIONS,
    COLORS: rooms.__COLORS,
    GLYPHS: rooms.__GLYPHS,
    MAP_PIXELS: semanticMapPixels(),
    LINKS: links,
    STAGE_LINKS: Object.fromEntries(Object.entries(stages.__STAGE_LINKS).map(([k, v]) => [k, v.teleporterNames])),
    TELEPORTERS: constants.TELEPORTERS,
}

const logicJson = {
    _source: { project: 'SOTN-Shuffler (Sestren, MIT)', commit: commits.shuffler },
    ROOMS_INFO: constants.ROOMS_INFO,
    ROOM_PRIORITY: constants.ROOM_PRIORITY,
    VALIDATIONS: constants.VALIDATIONS,
    LOCATIONS: constants.LOGIC.locations,
}

// ---- patcher.json ----

const patcherJson = {
    _source: { project: 'BIN-Patcher (Sestren, MIT)', commit: commits.binPatcher },
    ASSOCIATED_STAGES: sotnUs.__ASSOCIATED_STAGES,
    BOSS_ROOMS: sotnUs.__BOSS_ROOMS,
    BOSS_TELEPORTERS: sotnUs.__BOSS_TELEPORTERS,
    FAMILIAR_EVENTS: sotnUs.__FAMILIAR_EVENTS,
    FAMILIAR_OVERLAYS: Object.keys(sotnUs.FAMILIAR_OVERLAYS),
    LIVE_MAP_REPAINTS: sotnUs.__LIVE_MAP_REPAINTS,
    ORDERED_DEPENDENCY_NAMES: sotnUs.__ORDERED_DEPENDENCY_NAMES,
    ROOMS: sotnUs.__ROOMS,
    SECRET_MAP_TILE_REVEALS: sotnUs.__SECRET_MAP_TILE_REVEALS,
    TEMPLATE: readJson(path.join(bpDir, 'bins/sotn-us/data/change-dependencies-template.json')),
}

// ---- facts.json ----

// An object-array node: where it is, its element layout, aliases and any '_' values the generator reads
function arrayFact(nodePath, extraProps = []) {
    const node = nodeAt(nodePath)
    const meta = node.metadata
    const element = meta.element
    const [file, offset] = fileOf(toVal(meta.address))
    const props = {}
    for (const [name, info] of Object.entries(element.properties)) {
        if (!name.startsWith('_')) {
            props[name] = [toVal(info.offset), info.type]
        }
    }
    const fact = {
        file,
        offset,
        size: toVal(element.size),
        count: node.data.length,
        props,
        aliases: node.aliases ?? {},
    }
    for (const prop of extraProps) {
        fact[prop] = node.data.map((element) => element[prop])
    }
    return fact
}

// A single value. code: it is an operand inside a function (the mod handles those itself)
function valueFact(nodePath) {
    const meta = nodeAt(nodePath).metadata
    const [file, offset] = fileOf(toVal(meta.address))
    const fact = { file, offset, type: meta.element.type ?? meta.element.structure }
    if (functionAt(file, offset) !== null) {
        fact.code = true
    }
    return fact
}

// Throws if any byte of a table lands inside a function: the generator writes those as data
function assertData(name, file, offset, size) {
    for (let i = 0; i < size; i++) {
        const fn = functionAt(file, offset + i)
        if (fn !== null) {
            throw new Error(`${name} overlaps ${fn} in ${file}`)
        }
    }
}

const arrays = {}
const stageFiles = {}
for (const [stageName, stageInfo] of Object.entries(extraction.stages)) {
    stageFiles[stageName] = fileOf(toVal(sotnUs.STAGES[stageName]))[0]
    if (stageInfo.rooms?.data) {
        const extra = ['_rows', '_columns', 'top', 'left']
        arrays[`stages.${stageName}.rooms`] = arrayFact(`stages.${stageName}.rooms`, extra)
    }
    if (stageInfo.layers?.layerDefinitions?.data) {
        arrays[`stages.${stageName}.layers.layerDefinitions`] = arrayFact(`stages.${stageName}.layers.layerDefinitions`)
    }
    if (stageInfo.constants?.warpCoordinates?.data) {
        const warps = `stages.${stageName}.constants.warpCoordinates`
        arrays[warps] = arrayFact(warps)
    }
}
for (const nodePath of ['teleporters', 'bossTeleporters', 'secretMapTileReveals']) {
    arrays[nodePath] = arrayFact(nodePath)
}
for (const familiar of Object.keys(sotnUs.FAMILIAR_OVERLAYS)) {
    arrays[`familiarEvents.${familiar}`] = arrayFact(`familiarEvents.${familiar}`)
}
arrays['familiarEvents.noseDevil'].aliasesFrom = 'noseDemon'

// Raw teleporter entries (u16 words as on the disc) and their stage names, for getTeleporterChanges
const teleporterFact = arrays.teleporters
const teleporterNode = nodeAt('teleporters')
teleporterFact.entries = teleporterNode.data.map((element, index) => {
    const base = toVal(teleporterNode.metadata.address) + index * teleporterFact.size
    const word = (offset) => gdByte(base + offset) | (gdByte(base + offset + 1) << 8)
    return {
        sourceStageId: element.sourceStageId,
        targetStageId: element.targetStageId,
        raw: { playerX: word(0), playerY: word(2), roomOffset: word(4), targetStageId: word(8) },
    }
})

const values = {}
for (const nodePath of ['castleMap', 'castleMapReveals', 'castleMapColorPalettes.dra', 'castleMapColorPalettes.ric']) {
    values[nodePath] = valueFact(nodePath)
}
for (const name of Object.keys(extraction.specialSaveRooms)) {
    values[`specialSaveRooms.${name}`] = valueFact(`specialSaveRooms.${name}`)
}

for (const [name, fact] of Object.entries(arrays)) {
    assertData(name, fact.file, fact.offset, fact.size * fact.count)
}
assertData('castleMapReveals', values.castleMapReveals.file, values.castleMapReveals.offset,
    nodeAt('castleMapReveals').metadata.element.footprint)

// Every fixed address the dependency rules write to (code sites), named as file key and offset
const addresses = {}
const noteAddress = (gd) => {
    addresses[gd] = fileOf(gd)
    if (functionAt(...addresses[gd]) === null) {
        throw new Error(`dependency address 0x${gd.toString(16)} is not inside a function; it would be data`)
    }
}
for (const transformation of Object.values(patcherJson.TEMPLATE.miscellaneous)) {
    transformation.filter((a) => a.type === 'address').forEach((a) => noteAddress(a.address))
}
for (const repaint of patcherJson.LIVE_MAP_REPAINTS) {
    noteAddress(repaint.addressA)
    noteAddress(repaint.addressB)
}

const factsJson = {
    _source: {
        note: 'Positions and sizes read from the US disc (SLUS-00067) with BIN-Patcher',
        binPatcher: commits.binPatcher,
    },
    stageFiles,
    arrays,
    values,
    addresses,
}

// ---- fixed.json ----

// Applies BIN-Patcher changes to a fresh masked patch and returns every write as [gd, value, nodePath]
function patchWrites(changeFiles) {
    const patch = bpExtract.maskNodes(JSON.parse(JSON.stringify(extraction)), 'data')
    for (const changeFile of changeFiles) {
        for (const change of changeFile.changes) {
            bpChange.applyChange(patch, change)
        }
    }
    return collectWrites(patch)
}

// Walks the patch the way parsePatchNode does, so later writes to an address still win, and notes
// which node made each write
function collectWrites(patch) {
    const writes = []
    const ppf = new bpPpf.PPF()
    let current = ''
    // Each write goes through a throwaway PPF so its bytes can be listed with the node that made them
    ppf.write = (address, type, data) => {
        const one = new bpPpf.PPF()
        one.write(address, type, data)
        for (const [k, v] of Object.entries(one.writes)) {
            writes.push([parseInt(k, 16), v.at(-1), current])
        }
    }
    const walk = (node, nodePath) => {
        if (node.hasOwnProperty('metadata') && node.hasOwnProperty('data')) {
            current = nodePath
            bpPpf.parsePatchNode(ppf, node)
            return
        }
        Object.entries(node)
        .filter(([name, info]) => info !== null && !['data', 'metadata', 'aliases'].includes(name))
        .forEach(([name, info]) => walk(info, nodePath ? `${nodePath}.${name}` : name))
    }
    walk(patch, '')
    return writes
}

function fixedDiffs(fileNames) {
    const changeFiles = fileNames.map((name) => readJson(path.join(bpDir, 'bins/sotn-us/patches', name)))
    const finalByAddress = new Map()
    for (const [gd, value, nodePath] of patchWrites(changeFiles)) {
        finalByAddress.set(gd, [value, nodePath])
    }
    const data = {}
    const code = {}
    for (const [gd, [value, nodePath]] of [...finalByAddress.entries()].sort((a, b) => a[0] - b[0])) {
        const [file, offset] = fileOf(gd)
        // By where it lands, not by the node's name: some "functions" nodes in the patches are tile tables
        const fn = functionAt(file, offset)
        if (fn !== null) {
            const key = `${file} ${nodePath}`
            code[key] ??= { file, function: fn, path: nodePath, bytes: [] }
            code[key].bytes.push([offset, value])
            continue
        }
        if (nodePath.includes('.functions.')) {
            dataInFunctionNodes.add(`${file} ${nodePath}`)
        }
        if (gdByte(gd) === value) {
            continue
        }
        data[file] ??= {}
        data[file][offset] = value
    }
    const runs = {}
    for (const [file, bytesAt] of Object.entries(data)) {
        runs[file] = []
        let last = null
        for (const offset of Object.keys(bytesAt).map(Number).sort((a, b) => a - b)) {
            const hex = bytesAt[offset].toString(16).padStart(2, '0')
            if (last !== null && offset === last + 1) {
                runs[file].at(-1)[1] += hex
            }
            else {
                runs[file].push([offset, hex])
            }
            last = offset
        }
    }
    const credits = changeFiles.map((c, i) => ({ file: fileNames[i], authors: c.authors, description: c.description }))
    return { credits, files: runs, codeSites: Object.values(code) }
}

console.log('diffing the fixed patches')
const dataInFunctionNodes = new Set()
const fixedJson = {
    _source: {
        note: 'Bytes the seed-independent BIN-Patcher patches change, as [offset, hex] runs per file; code left out',
        binPatcher: commits.binPatcher,
    },
}
for (const [group, fileNames] of Object.entries(FIXED_PATCHES)) {
    fixedJson[group] = fixedDiffs(fileNames)
}
for (const site of dataInFunctionNodes) {
    console.log(`  kept as data (outside any function despite the node name): ${site}`)
}

// Every value the change dependencies set must land in a table facts.json describes (or nowhere, as in
// BIN-Patcher), or the port would silently drop a write
const resolvesTo = (property) => {
    const tokens = property.split('.')
    for (let i = 1; i < tokens.length; i++) {
        const node = tokens.slice(0, i).join('.')
        if (node in arrays) {
            return (tokens[i] in arrays[node].aliases) ? node : null
        }
        if (node in values) {
            return node
        }
    }
    return null
}
const isRealEntry = (property) => {
    // getCanonicalPath goes through 'data' (and an element index for a table) only for a real entry
    const canonical = bpChange.getCanonicalPath(property, extraction)
    const at = canonical.indexOf('data')
    if (at < 0 || !nodeAt(canonical.slice(0, at).join('.'))?.metadata) {
        return false
    }
    return at === canonical.length - 1 || typeof canonical[at + 1] === 'number'
}
for (const change of sotnUs.getChangeDependencies(patcherJson.TEMPLATE).changes) {
    for (const [name, actions] of Object.entries(change.evaluate)) {
        for (const action of actions.filter((a) => a.action === 'set' && a.type === 'property')) {
            const real = isRealEntry(action.property)
            const known = resolvesTo(action.property)
            if (real && known === null) {
                throw new Error(`${change.description} ${name}: ${action.property} is a table entry facts.json lacks`)
            }
            if (!real && known !== null) {
                throw new Error(`${change.description} ${name}: ${action.property} goes nowhere in BIN-Patcher`)
            }
        }
    }
}

// ---- write ----

fs.mkdirSync(outDir, { recursive: true })
const outputs = {
    'shuffler.json': shufflerJson,
    'logic.json': logicJson,
    'patcher.json': patcherJson,
    'facts.json': factsJson,
    'fixed.json': fixedJson,
}
for (const [name, content] of Object.entries(outputs)) {
    const text = JSON.stringify(content)
    fs.writeFileSync(path.join(outDir, name), text)
    console.log(`${name}: ${text.length} bytes`)
}
