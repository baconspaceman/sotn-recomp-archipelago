# Changelog

## 0.4.0

Test build of the shuffled castle.

- New options to shuffle the castle, ported from Sestren's SOTN Shuffler: `shuffle_rooms` lays out each area's rooms differently, `shuffle_area_connections` changes which areas lead to which. The logic, the pause map and the Map window follow the new layout.
- `map_area_colors`: with a shuffled castle, the pause map shows each area in its own colour
- With a shuffled castle, the Castle map from the Librarian shows the whole castle
- `starting_area`: sotn.io's fixes for Royal Chapel and Castle Keep starts

## 0.3.3

- Docked windows now actually come back docked after restarting the game (in 0.3.2 they came back floating together)

## 0.3.2

- Docked windows stay where you put them after restarting the game
- Right-click an item you don't have (Items) or a location (Map) to ask for a hint

## 0.3.1

- Map: your position updates right after using a warp room, instead of when you leave the room

## 0.3.0

First public test build.

- Runs on SymphonyRecomp; based on fdelduque's SotN apworld 0.8.16.1
- Places your seed while the game runs, with no patching
- AP items show as colored badges by importance
- Saves are tied to their seed and work offline
- In-game map, item tracker and text client
- DeathLink and all the original options, with clearer names
- Fixed:
  - items lost to progression balancing
  - a crash from certain enemy drops
  - missing powerful items
  - key items placed in spots where they could be missed
  - enemy stat text overlapping
  - Dracula's second form ignoring difficulty
  - some wrong enemy stats
  - starting gear that could include key items
  - start_inventory_from_pool
