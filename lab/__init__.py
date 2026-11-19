"""Tone Lab, rewritten.

The new implementation lives beside the old one (`tone_lab`) until it can
replace it. Its core idea: everything the app works on is a chain as the
Quad Cortex holds it. A design written by the assistant, a preset saved on
the unit and a preset imported from elsewhere are the same kind of thing, so
everything the app can do applies to all of them.

Layers, from the bottom:

- `catalog`: the unit's models and their parameters, the only place that knows
  how display values map to what the unit stores.
- `chain`: the chain model, reading it from a preset, and planning the writes
  that turn one chain into another.
- `measure`: one measurement of a piece of audio, used for everything.
- `knowledge`: tables learned from research and from the unit, with where each
  came from.
- `sim`: a simulated Quad Cortex, so that everything runs without hardware.
- `unit`: the real or simulated unit behind one guarded interface.
- `slots`: which slots are the app's, and the cache of built chains.
- `sounds`: sounds, songs, instruments and phrases, and where they are kept.
"""
