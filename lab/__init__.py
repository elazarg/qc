"""Tone Lab.

Everything the app works on is a chain as the Quad Cortex holds it: a design
written by the assistant, a preset saved on the unit and a preset imported from
elsewhere are the same kind of thing, so everything the app can do applies to
all of them.

Packages. `sound` is the bottom (only its designer looks up, at the player's IRs and captures in
`qc.assets`). `qc` and `hearing` build on it and on each other: the engine listens, and listening plays
through the rig. Everything else builds on those three.

- `sound`: what a sound is and how it changes. The unit's models (`catalog`),
  the chain and planning the writes between two chains (`chain`), the Shape
  knobs (`knobs`), the player's words (`words`), the designer and its
  instructions (`design`, `designer.md`), sounds, songs and phrases (`sounds`)
  and the assistant (`assistant`).
- `qc`: everything that talks to the Quad Cortex. The connection (`device`),
  the guarded unit (`unit`), the app's slots and the cache of built chains
  (`slots`, `rig`), the engine that holds the unit in a process of its own
  (`engine`, `host`), a simulated unit (`sim`), the unit's presets (`library`)
  and the player's IRs and captures (`assets`).
- `hearing`: listening without speakers. Audio over USB (`audio`), one
  measurement for everything (`measure`), test signals (`probe`), listening to
  a chain (`listen`) and what the player's guitar puts out (`profile`).
- `player`: what is learned about the player, from their choices (`taste`)
  and their listening history (`listening`, `spotify`). Generic code; what it
  works out from someone's data stays in their data folder.
- `desk`: the window (`window`) and the studio behind it (`studio`), with its
  parts: what is listened to and when (`listening`), requests and rounds
  (`requests`), marks and songs (`marks`), the guitar's takes and profile
  (`guitar`), what differs between A and B (`difference`), and the look
  (`theme`).
- `tools`, `learner`: the side windows and the Guitar Learner.
- `gear`: POD Go presets (`podgo`) and the Nano Cortex sheet (`nano`).
- `knowledge`: tables learned from research and from the unit, with where each
  came from; `cli`, `setup`, `migrate`: the command line, first run, and
  bringing in the earlier app's history.
"""
