# Distortion ladder, measured on the unit

Measured on 2026-11-20, CorOS 4.1.1, with `lab/probe.py`'s test signal played over USB at the level of a firmly
held note in one of the player's recorded phrases (Charvel, pickup setting not noted). Each chain was built from
empty, so every value it does not state is the unit's own default. Silent in the room.

What each column measures:

- **distortion**: power at the 2nd to 10th harmonics of a 196 Hz tone against the tone, in dB, at the player's level;
- **soft**: the same with the tone 12 dB softer;
- **IMD**: power at the sum and difference tones of 196 and 247 Hz played together, against the two notes;
- **even/odd**: even harmonics against odd ones;
- **compression**: from the phrase, how much of a 12 dB drop in the playing fails to come back (0 none, 1 all).

| Chain | distortion | soft | IMD | even/odd | compression | other |
|---|---|---|---|---|---|---|
| US TWN Vibrato, defaults, 412 V30 | −45.4 | −64.0 | −39.0 | −5.4 | 0.00 | |
| US DLX Vibrato, defaults, 412 V30 | −41.6 | −50.2 | −30.5 | 8.6 | 0.12 | |
| Brit Plexi 50 Normal, GAIN 2 | −29.7 | −49.4 | −24.1 | −5.0 | 0.14 | |
| Brit Plexi 50 Normal, GAIN 5 | −18.0 | −39.4 | −11.9 | 5.8 | 0.51 | |
| Brit Plexi 50 Normal, GAIN 8 | −17.9 | −21.4 | −10.2 | 3.1 | 0.66 | |
| Brit Plexi 50 Normal, GAIN 10 | −18.1 | −18.7 | −10.0 | 0.8 | 0.72 | |
| CA Duo Ch3 Modern, defaults | −20.3 | −21.9 | −9.8 | 1.0 | 0.80 | |
| Solo 100 Lead, defaults | −19.9 | −21.7 | −10.6 | −14.6 | 0.88 | |
| Green 808 OVERDRIVE 10 into US TWN | −29.9 | −33.2 | −14.5 | −16.7 | 0.69 | |
| Facial Fuzz FUZZ 10 into Plexi GAIN 3 | −19.7 | −19.6 | −9.8 | −4.7 | 0.99 | |
| VCA Comp alone | −54.0 | −70.0 | −35.9 | | 0.36 | |
| Legendary 87 alone | −67.6 | −70.0 | −51.6 | | 0.61 | |
| Room alone | −70.0 | −70.0 | −70.0 | | 0.00 | decay 0.23 s |
| Digital Delay alone | −70.0 | −70.0 | −50.5 | | 0.00 | echo 503 ms, −19 dB |
| Vintage Chorus alone | −70.0 | −70.0 | −70.0 | | 0.02 | level wobble 15 % |
| Tremolo alone | −70.0 | −70.0 | −70.0 | | 0.01 | level wobble 63 % |

−70 dB is the measurement's floor: no harmonic stood above the noise.

## What it shows

- Compression is not distortion. The Legendary 87 alone compresses 0.61, which the earlier, compression-based
  "drive" would have called overdriven, and adds no harmonics. Both are now measured and named separately.
- Played firmly, a driven amp's harmonics level off near −18 to −20 dB (Plexi at 5, 8 and 10, two high-gain amps,
  a fuzz). How far into distortion it is shows in the softer tone: the Plexi at 5 cleans up to −39 dB, at 10 it
  stays at −19.
- Linear blocks (reverb, delay, chorus, tremolo) add no harmonics, as they should; their own measures (decay,
  echo, wobble) find them.

## The words, set from this

| Word | Rule |
|---|---|
| clean | distortion below −38 dB |
| edge of breakup | distortion −38 to −26 dB |
| crunch | distortion above −26 dB and the softer tone below −30 dB (cleans up) |
| overdriven | the softer tone −30 to −20.5 dB |
| saturated | the softer tone −20.5 dB or more (stays distorted) |

The rule is an interpretation of measured values, fitted to sixteen chains and one player's level. More chains,
and the same ladder at each pickup setting, would make it firmer.

## Texture, measured on the same ladder

Measured again on 2026-11-20 with the harmonic orders (`lab/probe.py`): low orders are the 2nd and 3rd harmonics
against the tone, high orders the 5th to 10th, weighted is Shorter's n²/4 weighting, T3 the share of the power
in the 5th to 10th.

| Chain | low | high | high − low | weighted | T3 | texture |
|---|---|---|---|---|---|---|
| US TWN Vibrato | −45.4 | −70.0 | | −42.5 | 0.0000 | round |
| US DLX Vibrato | −42.8 | −48.9 | −6.1 | −37.6 | 0.0000 | round |
| Brit Plexi 50, GAIN 2 | −30.6 | −36.9 | −6.3 | −23.9 | 0.0002 | warm |
| Brit Plexi 50, GAIN 5 | −20.4 | −25.2 | −4.8 | −11.8 | 0.0030 | warm |
| Brit Plexi 50, GAIN 8 | −21.2 | −22.1 | −0.9 | −9.2 | 0.0061 | even |
| Brit Plexi 50, GAIN 10 | −21.8 | −21.4 | +0.4 | −8.8 | 0.0071 | even |
| CA Duo Ch3 Modern | −25.4 | −22.5 | +2.9 | −9.6 | 0.0056 | edged |
| Solo 100 Lead | −25.6 | −21.4 | +4.2 | −9.6 | 0.0072 | rough |
| Green 808 into US TWN | −33.0 | −32.9 | +0.1 | −21.4 | 0.0005 | even |
| Facial Fuzz into Plexi | −22.9 | −22.9 | 0.0 | −10.9 | 0.0050 | even |
| Compressors, reverb, delay, chorus, tremolo alone | ≤ −54 | ≤ −64 | | ≤ −49 | 0 | round |

Real amps through a cab never approach hard clipping's share of high orders (0.06): the share alone cannot tell
them apart. The balance between high and low orders does, from −6 dB (a Plexi at low gain) to +4 dB (a Soldano
lead), which is the research's texture axis (docs/TONE_RESEARCH.md, findings 2 and 7). The words:

| Word | Rule |
|---|---|
| round | order-weighted distortion below −30 dB |
| warm | high − low below −3 dB |
| even | −3 to +1.5 dB |
| edged | +1.5 to +4 dB |
| rough | +4 dB or more, or T3 at least 0.03 (hard clipping) |

Attack times on the same ladder were unreliable on driven sounds (a sustaining amp reaches its peak late, so the
10-90 % rise reads long: 168 ms on the Duo); they are shown but not yet used for words.
