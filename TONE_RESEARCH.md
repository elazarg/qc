# Tone shaping: what research says, and what it means for Tone Lab

A research run on 20 November 2026: five search angles, 22 sources fetched, 94 claims extracted, 25 checked
by three independent verifiers each (a claim died with two refutations), 22 confirmed, 3 refuted, merged into
11 findings below. Each finding ends with what it suggests for the app. Confidence is the run's own.

The evidence is uneven. Most perception research used isolated orchestral or synthetic tones heard alone at
equal loudness, not distorted guitar in a band. The guitar-specific sources are few and small. Large parts of
the question found nothing that survived checking (see the end), which says more about the sources than the
subject.

## What is perceived

**1. Tone words collapse onto a few axes, and "body" is the weakest of them.** *(high)*
Thirty adjectives rated by Greek and English speakers fell on three factors explaining 82% of the variance:
*luminance* (bright, brilliant against dark, deep), *texture* (rough, harsh against round, warm, soft) and
*mass* (full, rich, thick). Luminance and texture lined up with perceived dimensions; mass did not. "Warm"
sits on the texture axis, not opposite "bright". Fullness may still matter for chords and mixes (Alluri and
Toiviainen 2010).
Zacharakis, Pastiadis, Reiss, *Music Perception* 2014 and 2015.
- **For the app:** a sound's character is better shown and moved as a point on a brightness × texture plane
  than as many separate adjective sliders. The Warmth knob currently means dark-against-bright; research puts
  warm against rough. Body needs checking against players' own judgments before it is trusted.

**2. Texture and brightness have different acoustic correlates.** *(high)*
Harshness follows how energy is spread across the harmonics (normalised harmonic centroid, tristimulus 3,
spectral spread): stronger high partials sound harsher. Brightness relates to absolute spectral content, plus
odd/even balance and inharmonicity.
Same sources.
- **For the app:** brightness must not double as a harshness or fizz meter. Measure texture from the
  normalised harmonic distribution (the test tone already gives each harmonic) and brightness from band
  energy.

**3. For distorted guitar, spectral centroid mis-ranks brightness.** *(medium)*
Experienced guitarists comparing Helix presets built from the same DI sometimes heard the lower-centroid clip
as brighter. The authors suggest adding F0 and harmonic-to-noise ratio.
Sutar, Naradowsky, Miyao, "Does it Chug?", NLP4MusA 2024 (2038 judgments, 38 listeners).
- **For the app:** treat the brightness word as provisional; combine several features; let the player's A/B
  choices correct it.

**4. Timbre is several dimensions plus features particular to each sound, and listeners weight them
differently.** *(high)*
The robust shared correlates are spectral centroid, attack time and spectral fine structure; they stay
independent of one another. Listeners fall into classes that weight them differently.
McAdams 2017 review (Grey 1977; McAdams et al. 1995; Caclin et al. 2005).
- **For the app:** describe a sound as a vector (brightness, texture, attack, and what is particular to it),
  learn each player's weights from their marks, and add an attack measure beside the spectral ones.

**5. Words miss what happens in time.** *(medium)*
One perceptual dimension was driven by attack time, temporal centroid and how harmonics change over time, and
no adjective named it.
Zacharakis et al. 2015; McAdams 2017.
- **For the app:** attack, sag, bloom, sustain and touch response must be measured from envelopes and dynamic
  response (compression, decay, tail are a start), not inferred from spectral words. Dynamic words (punchy,
  snappy, smooth) belong in the remark lexicon.

**6. A guitar-effects vocabulary of 19 words, grouped by effect family.** *(low)*
Distortion: crunchy, crushed, dirty, fuzzy. Filter: bright, dark, fat, resonant, thin. Dynamics: punchy, sharp,
soft, smooth, tight. Oscillation: fluttery, jittery, shimmering, stuttering, wah-wah.
Cameron and Blackwell 2026 preprint; words taken from product descriptions, not listening tests.
- **For the app:** seed words for requests and marks; not axes.

## Measuring

**7. One distortion figure does not predict how distorted something sounds.** *(medium)*
In a listening test THD (r = −0.48) and IMD (r = −0.45) barely tracked rated distortion; psychoacoustic
metrics did (DS r = −0.95, Rnonlin r = 0.95). Energy in high harmonic orders (5th and above, as from hard
clipping) is heard far more than the same energy in the 2nd and 3rd, which the fundamental masks.
de Santis and Henin 2007 (Aalborg thesis, nine listeners, unwanted distortion), consistent with Moore and Tan
(JAES 2003/04) and Geddes and Lee (AES 2003).
- **For the app:** keep the harmonic-order profile (low orders against high orders) and add an order-weighted
  figure (Shorter's n² weighting or the GedLee metric) beside the plain one. Intentional guitar distortion was
  not what was tested.

## Craft

**8. Amp controls interact.** *(medium)*
On a Mesa Rectifier the tone stack sits between gain stages: treble strongly changes the gain.
MusicRadar; the Mesa Dual/Triple Rectifier manual ("the setting of the TREBLE control greatly affects the
strength of the gain control").
- **For the app:** a knob's effect cannot be assumed from a table. Measure after a turn (the unit can be
  reamped silently) and correct, per amp.

**9. Famous tones depend on where things sit, not only on EQ totals.** *(medium)*
Iommi's *Iron Man* tone: a P90 SG Special into a modified Rangemaster that boosted and cut bass, into a Laney
Supergroup's Bass input with Presence, Middle, Treble and Gain at 10 and Bass at 0, through Celestion G12-25M:
low end cut before and inside the amp, a thick hollow midrange.
Guitar World "Tonal Recall", tracing to Iommi's 1974 Guitar Player interview.
- **For the app:** song recipes should record what comes before the amp and the input and pickup used.

## The player's instrument

**10. The guitar changes what reaches the unit, in predictable physical ways.** *(high)*
Cable capacitance (about 50–150 pF/m) lowers a passive pickup's resonance, so treble depends on cable length.
The tone knob only cuts treble; a larger capacitor gives a lower, more muffled roll-off. Turning the volume down
changes the response as well as the level. Lower pot resistance damps the resonance (warmer). Humbuckers have
lower resonance and less treble than single coils; parallel wiring lowers inductance against series: less
output, higher resonance, thinner lows.
Rod Elliott (sound-au.com); Frank Falbo, *Premier Guitar* 2015; standard pickup physics (Lemme, Zollner).
- **For the app:** the guitar profile should shift what the designer aims for: gain and EQ for this guitar,
  this setting, this cable. The phrases already carry the guitar's colour.

## Tools like this one

**11. AI tone generators: several candidates, and a warning.** *(medium)*
Positive Grid's Reactor returns a handful of presets per text, image or audio prompt. Its reviewer found the
newer modes inconsistent and warned against leaning on AI to rank presets.
Guitar World review (June 2026); Sound On Sound (August 2026).
- **For the app:** the round of three, checked by measurement before it is shown, is the right shape; the
  player ranks; be modest about matching from audio.

## Refuted

- That musicians and non-musicians rate the same guitar as opposite in brightness (1–2).
- SRV's *Pride and Joy* TS9 settings as published (0–3).
- That the Reactor's audio matching was "not close" (0–3; the reviewer was less damning).

## What found no verified evidence in this run

Context (in a band mix against alone, listening level and equal-loudness curves, headphones against monitors),
loudness bias and level-matched blind comparison, listening fatigue, how many options to show, microphone
placement and blending, effect order, scenes, common mistakes (too much gain, scooped mids), correlates of
compression and roughness, and manufacturer documentation from Neural DSP, Line 6, Fractal and Kemper.
Established practice, such as matching levels before comparing, is still sound; it was not checked here. A
second run aimed at these would fill the largest gaps.

## Proposals, in order of what they would change most

1. **Texture as a measure and a control.** From the test tone's harmonics: low-order against high-order energy,
   normalised harmonic centroid, an order-weighted distortion figure. Show "round ↔ rough" beside brightness.
   Replace Warmth and Smoothness by a two-dimensional *brightness × texture* pad on the Shape panel (one point
   moves both, each through the controls that serve it); keep Presence and Mids as they are.
2. **Attack as a measure.** Onset time and temporal centroid from the phrase, and a response to picking
   dynamics; a *Pick attack* knob that is checked against it.
3. **Closed-loop knobs.** After a turn, listen again silently and correct the move if the measured quality did
   not change as intended (interacting controls, finding 8).
4. **The player's weights.** Learn from marks and A/B choices which measured qualities predict what the player
   keeps, and rank and describe candidates with those weights; correct the brightness word from the same.
5. **The guitar in the design.** Give the designer the guitar profile (output per pickup setting, brightness)
   and ask for gain and EQ suited to it; record the input and pickup in recipes.
6. **Second research run** on comparison, context and manufacturer documentation.
