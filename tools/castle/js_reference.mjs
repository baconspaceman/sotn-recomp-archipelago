// Runs the JS castle shuffler for one seed and lists the bytes BIN-Patcher would write, so
// check_parity.py can hold the Python port against it.
//
// usage:
//   node tools/castle/js_reference.mjs --shuffler DIR --seed NAME --out FILE [--disc TRACK1.BIN | --extraction FILE]
//        [--rooms on|off] [--stages on|off] [--area-colors on|off] [--labels on|off] [--nose-devil-fix on|off]
//
// The extraction (BIN-Patcher's reading of the disc) is --extraction, else made from --disc, else the
// build/patcher/extraction.json that the checkout's setup.sh writes.
//
// It runs `node shuffle multi` in the SOTN-Shuffler checkout (solver, rewards, music and file-select
// hints off; the patches the castle shuffler needs on), then applies the seed's changes and the change
// dependencies with BIN-Patcher in this process. The output JSON has:
//   changes   the seed file's changes (room positions, teleporters, map picture, reveal mask)
//   writes    [gameDataAddress, byte, nodePath] for every byte written, last write per address
// With --nose-devil-fix on, BIN-Patcher's familiar table for the nose devil (TT_006) gets the aliases
// it is missing, as the Python port does.

import fs from 'fs'
import os from 'os'
import path from 'path'
import { execFileSync } from 'child_process'
import { pathToFileURL } from 'url'

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

const args = parseArgs(process.argv.slice(2))
const on = (name, fallback) => (args[name] ?? fallback) === 'on'
const shufflerDir = path.resolve(args.shuffler)
const bpDir = path.join(shufflerDir, 'lib', 'BIN-Patcher')
const rooms = on('rooms', 'on')
const stages = on('stages', 'on')
const areaColors = on('area-colors', 'on')
const labels = on('labels', 'on')
const noseDevilFix = on('nose-devil-fix', 'on')

const patchDir = 'lib/BIN-Patcher/bins/sotn-us/patches'
const patches = [
    'assign-power-of-wolf-relic-a-unique-id.json',
    ...(areaColors ? ['customize-map-colors.json'] : []),
    'normalize-abandoned-mine.json',
    'normalize-alchemy-laboratory.json',
    'normalize-castle-entrance.json',
    'normalize-clock-tower.json',
    'normalize-long-library.json',
    'normalize-marble-gallery.json',
    'normalize-olroxs-quarters.json',
    'normalize-underground-caverns.json',
]

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'castle-js-'))
const importFrom = (file) => import(pathToFileURL(path.join(bpDir, file)).href)

// The extraction: given, or the one setup.sh leaves in the checkout, or made here from --disc
let extractionPath = args.extraction ?? path.join(shufflerDir, 'build', 'patcher', 'extraction.json')
if (!args.extraction && args.disc) {
    const { processBinary } = await importFrom('bins/sotn-us/sotn-us.js')
    const { GameData } = await importFrom('src/common.js')
    const cwd = process.cwd()
    process.chdir(bpDir) // processBinary reads its templates from relative paths
    const made = processBinary(new GameData(fs.readFileSync(args.disc))).extraction
    process.chdir(cwd)
    extractionPath = path.join(tmp, 'extraction.json')
    fs.writeFileSync(extractionPath, JSON.stringify(made))
}
const seedFile = path.join(tmp, 'seed.json')
const cli = [
    'shuffle.js', 'multi',
    '-e', extractionPath,
    '-o', seedFile,
    `--seed=${args.seed}`,
    `--hinter.stageLinks=${labels}`,
    '--patcher.on=true',
    ...patches.map((p) => `--patcher.list=${patchDir}/${p}`),
    `--roomShuffler.on=${rooms}`,
    `--stageShuffler.on=${stages}`,
    '--rewardShuffler.on=false',
    '--musicShuffler.on=false',
    '--solver.on=false',
]
const started = Date.now()
execFileSync(process.execPath, cli, { cwd: shufflerDir, stdio: ['ignore', 'ignore', 'inherit'], maxBuffer: 1 << 30 })
const shuffleSeconds = (Date.now() - started) / 1000
const seed = JSON.parse(fs.readFileSync(seedFile, 'utf8'))
const extraction = JSON.parse(fs.readFileSync(extractionPath, 'utf8'))
fs.rmSync(tmp, { recursive: true, force: true })

const { maskNodes } = await importFrom('src/extract.js')
const { applyChange } = await importFrom('src/change.js')
const { PPF, parsePatchNode } = await importFrom('src/ppf.js')
const { getChangeDependencies } = await importFrom('bins/sotn-us/sotn-us.js')

const patch = maskNodes(extraction, 'data')
if (noseDevilFix) {
    const aliases = JSON.parse(fs.readFileSync(path.join(bpDir, 'bins/sotn-us/data/aliases.json'), 'utf8'))
    patch.familiarEvents.noseDevil.aliases = aliases.familiarEvents.noseDemon
}
for (const change of seed.changes) {
    applyChange(patch, change)
}
const templatePath = path.join(bpDir, 'bins/sotn-us/data/change-dependencies-template.json')
const template = JSON.parse(fs.readFileSync(templatePath, 'utf8'))
for (const change of getChangeDependencies(template).changes) {
    applyChange(patch, change)
}

// Walk the patch in parsePatchNode's order, noting which node writes each byte
const final = new Map()
let current = ''
const ppf = new PPF()
ppf.write = (address, type, data) => {
    const one = new PPF()
    one.write(address, type, data)
    for (const [k, v] of Object.entries(one.writes)) {
        final.set(parseInt(k, 16), [v.at(-1), current])
    }
}
const walk = (node, nodePath) => {
    if (node.hasOwnProperty('metadata') && node.hasOwnProperty('data')) {
        current = nodePath
        parsePatchNode(ppf, node)
        return
    }
    Object.entries(node)
    .filter(([name, info]) => info !== null && !['data', 'metadata', 'aliases'].includes(name))
    .forEach(([name, info]) => walk(info, nodePath ? `${nodePath}.${name}` : name))
}
walk(patch, '')

const writes = [...final.entries()].sort((a, b) => a[0] - b[0]).map(([gd, [value, nodePath]]) => [gd, value, nodePath])
// Only the shuffler's own changes (the patch files are compared through their bytes)
const patchChangeCount = patches.reduce((n, p) => {
    return n + JSON.parse(fs.readFileSync(path.join(shufflerDir, patchDir, p), 'utf8')).changes.length
}, 0)
const ownChanges = seed.changes.slice(patchChangeCount)
fs.writeFileSync(args.out, JSON.stringify({ seed: args.seed, shuffleSeconds, changes: ownChanges, writes }))
