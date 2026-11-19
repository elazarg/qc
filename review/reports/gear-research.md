# Reader's report: the Quad Cortex input, the pickups, and the other gear

One of nine reports behind `REWRITE_REVIEW.md`. Web research on 2026-11-19, no code read. Labels: [O] official, [M] measured by a named third party, [C] community. "Derived" marks the reader's own arithmetic. Several pages refused the reader (charvel.com, fender.com, zzounds). Items marked "search snippet" were not opened directly.

## 1. Quad Cortex instrument input

- [O] Impedance: 1 MΩ default, adjustable 10 kΩ–10 MΩ, TS/Instrument only; maximum input gain +60 dB; USB inputs 1/2 carry dry Input 1/2, 3/4 wet; 48 kHz/24-bit. https://neuraldsp.com/manual/quad-cortex
- Discrete impedance steps, the lower bound of the Level range, official per-value recommendations, an official input dBu specification: not found.
- [C] 1 MΩ unless a fuzz is first; lower values roll off treble (one user preferred 232 kΩ on a Tele). https://unity.neuraldsp.com/t/quad-cortex-input-impedance-explanation/8410
- Neural DSP support figures, relayed by user MirrorProfiles: plugins assume a 1 Vp sine = −13 dBFS (0 dBFS = 12.2 dBu). The same sine into the Quad Cortex at Input Level 0, 1 MΩ, reads −15.1 dBFS, so add about +2.3 dB to the unit's USB DI; later posts say +2.8 dB and "15 dBu over USB". https://unity.neuraldsp.com/t/optimal-input-level-for-highest-accuracy-when-using-ndsp-plugins/11048
- [C] Two notes lists the Quad Cortex input as +14.8 dBu. https://helpdesk.two-notes.com/portal/en/kb/articles/calibrating-genome-s-input-your-audio-interface
- Derived: 0 dBFS on the unit is about 14.3–15 dBu (5.7–6.2 V peak) at Level 0. A −18 dBFS peak is about 0.72–0.78 V peak; −36 dBFS RMS is roughly 90 mV. That is normal for a medium-hot humbucker (see 3 and 4), not weak.
- [C] The dry USB level follows the Input Level setting (forum search snippet; thread not pinned down). The figures only hold at Level 0.

## 2. Why presets sound wrong

- An official input-level target for hot versus weak pickups: not found. The manual's only target is capture input "around −12 dB".
- [C] Leave Input Level at 0 so models react like the amp; raising it acts as a boost; fix level at the output block, near but under clip. Relayed from Neural: the headphone output has about +6 dB internal gain, so keep headphone level 0 to −3 dB. https://unity.neuraldsp.com/t/new-qc-owner-help-me-understand-volume/14747
- [C] Opinions range from 0 dB as reference, to "hard strums peak −12 dB", to as hot as possible. https://unity.neuraldsp.com/t/what-is-a-proper-input-level-and-how-to-choose-the-right-impedance/7401
- Moderator MP_Mod: demos are DAW recordings, not in-room sound; check monitor settings and the cab/IR; add high and low cuts. User advice: cab HPF about 80 Hz, LPF about 6 kHz (4–5 kHz darker). https://unity.neuraldsp.com/t/quad-cortex-sounds-very-different-from-demos/20687
- [C] Build on FRFR at low volume, re-check at gig volume; headphones differ from FRFR (search snippet, thread not opened). https://unity.neuraldsp.com/t/quad-cortex-headphone-vs-main-xlr-outputs-sonic-difference/19859

## 3. Pickups and the DK24's switching

- [O] Seymour Duncan strum-rig output data (Scott Olson): Full Shred bridge 724 mV, APH-1n 452, JB 737, '59 bridge 593, Distortion 792, Custom 784. https://forum.seymourduncan.com/threads/seymour-duncan-mv-data.285455/page-4
- Derived: the Full Shred is within 0.2 dB of a JB and 1.7 dB over a '59 bridge; the neck pickup is about 4 dB below the bridge.
- [O] Current Duncan pages: Full Shred bridge 14.1k, Trembucker 14.8k, Alnico 5; APH-1n 7.5k, Alnico 2. https://www.seymourduncan.com/?p=2054 , https://www.seymourduncan.com/pickup/alnico-ii-pro-hb-neck
- Duncan tone chart (search snippets of retailer and forum copies): SH-10b 14.6k, 5.5 kHz; APH-1n 7.6k, 7.1 kHz; JB 16.4k, 5.5 kHz. https://www.zzounds.com/item--SEMSH10B
- Inductance for the Full Shred and APH-1: not found. [C] old JBs measured 8.0–8.1 H. https://forum.seymourduncan.com/threads/for-the-jb-nerds-inductance-and-dc-resistance-values-for-older-jb-models.6325836
- Search snippets only: EMG 81 resonance 2.25 kHz; Gibson 498T 14.2k (https://www.gibson.com/en-US/p/Pickup/PU498TDB4/PU498TDB4). 490R: not found.
- Charvel (page refused; via search snippet): 1 bridge, 2 both outer coils, 3 bridge and neck, 4 both inside coils, 5 neck; pickups TB-10 and APH-1N. https://www.charvel.com/gear/shape/dk/pro-mod-dk24-hh-2pt-cm
- Series or parallel wiring of positions 2 and 4: not found.

## 4. Typical DI levels

- [M] Rod Elliott (ESP), 10 MΩ load, light touch, RMS (peak): humbucker bridge open-E chord 128 mV (850 mV), low E 120 mV (800 mV); single-coil neck chord 76 mV (450 mV); high E 12–20 mV (50–300 mV). Hard playing adds at least 6 dB. https://www.sound-au.com/articles/guitar-voltage.htm
- [O] Duncan passive humbuckers span about 450–810 mV on its rig; whether peak or RMS is not stated (link in 3).
- [C] Single coils about 12 dB below humbuckers on an open D chord. https://forum.fractalaudio.com/threads/guitar-output-levels.62948/
- [C] One scope blog reports only 10–30 mV peak per picked open A string, so methods vary widely. https://tomsguitarprojects.blogspot.com/2014/12/electric-guitar-output-voltage-levels.html
- DI-box maker figures: not found.

## 5. Nano Cortex

- [O] Fixed chain: gate, 2 pre-effect slots, 1 Neural Capture, 1 IR, 3 post-effect slots; 50+ effects; no amp models; 64 presets, 256 captures, 256 IRs. Quad Cortex presets are incompatible; captures load both ways through the Cortex Cloud app over Bluetooth. No desktop editor. https://support.neuraldsp.com/help/nano-cortex-r-faq
- [O] NanOS 2.2.0 plays V2 captures made on the Quad Cortex. https://neuraldsp.com/nano-cortex-updates/nanos-2-2-0-is-now-available
- [O] MIDI over USB-C and TRS: program change 0–63 recalls presets; CC 34–41 bypass slots, CC 1 expression, 42 tap, 43 tuner. Loading content over USB from a computer is not documented. https://neuraldsp.com/manual/nano-cortex
- Practical route: capture the Quad Cortex amp and drive tone (or reuse the same cloud capture), download it to the Nano, then rebuild cab/IR and effects by hand.

## 6. POD Go

- [C] `.pgp` is plain JSON. `.pgb` starts "AF6L" and holds two zlib-compressed sections that decode to JSON (globals plus factory presets, and user presets). No confirmation from Line 6 staff. https://line6.com/support/topic/63502-fileformats-custom-tools-and-api/
- An open-source `.pgp` reader: not found; JSON parsing suffices.
- [O] Line 6 model list with a "Based on" column (for example Brit 2203 = Marshall JCM-800, Minotaur = Klon Centaur). https://line6.com/podgo-models/
- [O] Neural DSP's equivalent list for the Quad Cortex, so models can be matched through the real originals. https://neuraldsp.com/device-list

## 7. Studio Pro stem separation

- [M] Sound On Sound on Studio One Pro 7: four stems (Vocals, Drums, Bass, Other), placed in a folder under the disabled original, with an optional remix of selected stems. "Largely clean" but with artefacts and bleed. No guitar stem; guitar falls in Other (the reader's inference). https://www.soundonsound.com/reviews/presonus-studio-one-pro-7?amp
- [M] Sound On Sound on 8.1: the Moises panel offers up to 20 instrument options against four locally, and the Hi-Fi quality is "remarkable" (the rest is paywalled). https://www.soundonsound.com/reviews/fender-studio-pro-8-1
- [O] Moises is cloud-based; the free tier is 10 separations a month with four stems. https://www.moises.ai/products/integrations/fender-studio-pro/
- Whether a dedicated electric-guitar stem is available inside Studio Pro, and documented stem export options: not found.
