// Module hooks for the castle data tools: they read constants that SOTN-Shuffler and BIN-Patcher keep
// private to their modules. Nothing is written into the clones; the source is changed in memory as
// Node loads it.
//
//  - Appends `export { X as __X, ... }` for the private tables listed in EXPOSE.
//  - shuffle-rooms.js loaded with the query "?semantic": every value in COLORS is replaced by
//    "@<colour name>" just before MAP_PIXELS is built, so the dumped MAP_PIXELS (including the default
//    drawings added at load time) says which colour each command means instead of a palette index.

import { registerHooks } from 'node:module'

const EXPOSE = {
    '/src/shuffle-rooms.js': ['COLLISIONS', 'COLORS', 'GLYPHS'],
    '/src/shuffle-stages.js': ['LINKS', 'STAGE_LINKS'],
    '/bins/sotn-us/sotn-us.js': [
        'ASSOCIATED_STAGES',
        'BOSS_ROOMS',
        'BOSS_TELEPORTERS',
        'FAMILIAR_EVENTS',
        'LIVE_MAP_REPAINTS',
        'ORDERED_DEPENDENCY_NAMES',
        'ROOMS',
        'SECRET_MAP_TILE_REVEALS',
    ],
}

const SEMANTIC_ANCHOR = 'export const MAP_PIXELS = {'

registerHooks({
    load(url, context, nextLoad) {
        const result = nextLoad(url, context)
        const [path, query] = url.split('?')
        const suffix = Object.keys(EXPOSE).find((s) => path.endsWith(s))
        if (!suffix) {
            return result
        }
        let source = result.source.toString()
        if (query === 'semantic' && suffix === '/src/shuffle-rooms.js') {
            if (!source.includes(SEMANTIC_ANCHOR)) {
                throw new Error('MAP_PIXELS declaration not found in shuffle-rooms.js')
            }
            source = source.replace(
                SEMANTIC_ANCHOR,
                'for (const __k of Object.keys(COLORS)) { COLORS[__k] = "@" + __k }\n' + SEMANTIC_ANCHOR,
            )
        }
        const names = EXPOSE[suffix]
        source += '\nexport { ' + names.map((n) => `${n} as __${n}`).join(', ') + ' }\n'
        return { ...result, source, shortCircuit: true }
    },
})
