# Tone Lab for Quad Cortex

Open **Tone Lab** from Start or **Tone Lab.lnk** in this folder. Opening it again restores the existing workspace. The dark workspace keeps tone shaping, live input, recorded phrases, and A/B in one window. Press **Ctrl+K** for the request field or use **Win+H** to dictate.

- Click anywhere on **A** or **B** to select the Quad Cortex tone and its knob settings. **Guitar** and **▶ Phrase** feed it live guitar or the dry recording. A/B keeps the phrase at its current position. Loading a different hardware chain can briefly interrupt the output. Guitar pauses the phrase and **▶ Phrase** resumes it. **■** or **Esc** releases the unit.
- **Volume** moves the listening level of A and B together after the effects. Use **Quieter**, **Louder**, **Reset**, or **Mute**, or ask for “a little louder.” Captured comparisons measure both clips automatically. On the Quad Cortex, let **▶ Phrase** loop through each tone to set their common level. **Guitar** can use those trims, with upward adjustment capped at the preset’s native output level. **Manual trim** plays tones at their relative preset levels. [Quad Cortex lane output controls](https://neuraldsp.com/manual/quad-cortex)
- **Takes** changes the performance. **Details** plays captured wet audio through Windows and can capture a new version for comparison. Captured clips on the same passage share a cursor.
- **Ask** sends new tone designs to B, including during a loop. New drafts include extra shaping stages where the signal chain has room. On an older draft, **Add shaping controls** creates a new version on B with those stages available. A remains the comparison tone.
- **Sounds** browses the actual Quad Cortex inventory. Choose a preset and scene for A or B, replace the cabinet in a draft with one of your IRs, or try a capture with the appropriate cabinet setup. **Save to QC** creates a native preset in the dedicated **Tone Lab** setlist. Subsequent auditions recall that saved sound.
- **Lab** offers two-dimensional controls, marked positions and sweeps. Use recorded A/B measurements to see the differences in spectrum, dynamics, stereo energy and note tails. **Setup** contains guitar calibration, gestures and soundcheck.
- **QC → Open Cortex Control** handles detailed hardware editing. Tone Lab preserves drafts, takes and cached playback while the editor is open. Direct hardware renders and saving require a handoff. Audio-only recording can retain the raw channels during editing.

Temporary work uses the two existing scratch slots in **My Presets**. Drafts use **Tone Lab Preview**, with the draft title on the active scene. During live A/B, Tone Lab can also use **Tone Lab 32H** to keep a second working copy. It uses that slot when it is empty or already contains its own working copy. Tone Lab restores the original preset and scene when finished. Permanent saves use a fresh empty slot in **Tone Lab**.

Rig sources appear under **Details** when available. See [the hardware capabilities and inventory](QC_CAPABILITIES.md) and [the interaction research](INTENT_AND_LANDSCAPE.md).

## Guitar Learner

Open **Guitar Learner** beside the guitar selector. The dedicated Charvel window starts with the DK24 five-way pickup blade and mini-toggle. Position 1 is bridge and 5 is neck, with the mini-toggle up for Parallel and down for Series on the reference HH 2PT wiring. Drag the numbered volume and tone dials, click the blade position and mini-toggle, then record or choose a dry take. The catalog photographs illustrate the two supported control layouts. [Charvel DK24 HH 2PT](https://charvel.com/gear/series/pro-mod/pro-mod-dk24-hh-2pt-cm/2969411519) · [Charvel DK24 HSS 2PT](https://charvel.com/gear/shape/dk/pro-mod-dk24-hss-2pt-cm/2969433519)

- Record, choose a take, or open a dry WAV. The learner checks the clip as soon as it opens. Match the board to the guitar, then choose **Teach with this clip** or **Keep as check**. Drag on the waveform to focus on one setup in a longer clip. **Dataset** shows the recordings, their settings, and whether they teach, count as checks, or await tags.
- **Listen live** follows the most recent usable dry audio. **Keep recent clip** opens a copy in the same review flow. When a possible change is detected, keep either side to tag it. **Baseline guide** suggests a short comparison phrase followed by ordinary playing. [Guitar Learner design](GUITAR_LEARNER_DESIGN.md)

## Guitar calibration and gestures

Open **Setup → Guitar calibration** for the single-control profiles used by live gestures. Choose **Pickup selector**, **Volume knob**, or **Tone knob** for the selected guitar. Set the control on the guitar, select the matching label, and press **Record**. Repeat the same sustained single notes at a second setting while keeping the other controls and technique steady. Choose **Learn settings**, then **Record a fresh check** to see which setting Tone Lab hears. Pickup estimates depend on where you pick, and volume estimates depend on input gain and how hard you play. [Pickup and plucking-point estimation study](https://qmro.qmul.ac.uk/xmlui/bitstream/handle/123456789/31405/Dixon%20Pickup%20position%20and%20plucking%202017%20Published.pdf?isAllowed=y&sequence=1)

Enable **Show estimate while playing guitar** before starting **Guitar** to see the estimate during live playing. To control a tone direction, open **Gesture mapping**, choose a starting state, an excursion, and a direction. Rehearse A → B → A with a sustained note or repeated notes at each position. **Arm mapped control for the next run** permits bounded changes for the first 20 seconds. The monitor analyzes dry USB input in memory. Observation and arming are off on app launch.

The tone-pot compensation experiment runs offline. Record the same phrase with the same notes in each half of the clip, once with the tone open and once rolled back. `python -m tone_lab test-compensation open.wav rolled.wav compensated.wav` tries a shelf capped at +6 dB and applies it to a new WAV only when it improves the held-out half. This measures an approximate correction before auditioning the effect chain. It does not reconstruct missing harmonics or compensate live.

## Sound check

- **Input and output** measures the recorded DI peak, wet loudness, estimated true peak, and samples near full scale. Play the hardest attack you expect at the gig when making the check take. USB measurements describe the captured signal. Check the Quad Cortex input meter and the mixer separately for headroom along the live path. [Quad Cortex manual](https://neuraldsp.com/manual/quad-cortex) · [Yamaha gain staging](https://hub.yamaha.com/proaudio/livesound/gain-staging/)
- **Relative preset levels** measures raw reamps of the same phrase and proposes a post-chain trim. The default plan uses the quieter usable render as an anchor. For an intentional lift, `python -m tone_lab levels --comparison <folder> --reference A --offset B=2` plans B two dB above A while leaving peak headroom. The plan measures the recorded excerpt. A band soundcheck is the way to judge whether a lead actually comes forward. [ITU-R loudness and true-peak measurement](https://www.itu.int/rec/R-REC-BS.1770-5-202311-I)
- **Stage EQ preview** makes matched-level variants with a low cut and a gentler top end as separate choices. Listen against the bass, drums, and vocal before choosing either. Use pre-drive EQ to change how the amp saturates, post-chain EQ to shape the finished guitar tone, and output-specific Global EQ for a shared speaker or venue correction. The previews and trim plans leave the Quad Cortex unchanged. [Yamaha band PA guide](https://usa.yamaha.com/audio/portable-pa/explore/pa-beginners-guide/setting/band-pa.html) · [Quad Cortex Global EQ](https://neuraldsp.com/manual/quad-cortex)

## Optional workbench

The workbench is available via `python -m tone_lab ui` in the local environment. It exposes USB diagnostics, routing comparisons, candidate audition controls and session files. Its **Offline demo** builds three simulated candidates without starting playback. Its **Check Quad Cortex** action reads the current preset and opens the ASIO driver without starting a stream. The **Play** controls use the Windows default output.

## Compare and give feedback

Use **Change** in Current take to revisit a recording. **Dry** and **Original wet** play the recorded inputs. **Compare versions** labels every audition and offers an optional **Blind** view. Use **Older** and **Newer** to move among comparisons. A listening note remembers the version you just played.

When adjusting the live tone, **More jazz**, **Less delay**, and **Darker** work directly from the request box. They move the relevant knob while live control is running. With a saved take available and live control stopped, **Compare on this take** renders the proposed change against the recorded wet sound.

### Live controls and the tone advisor

Drag, scroll, or use arrow keys to turn a knob. The controls combine their changes against the fixed preset baseline. **Reset** returns to that baseline. Drive at its minimum backs off gain and bypasses compatible active drive pedals. A modeled capture can retain the distortion of its source. Choose a clean model or capture when you want a genuinely clean starting point.

- **Loop take** feeds the same dry passage into the Quad Cortex and monitors through its normal outputs. Start with the Quad Cortex volume low. A separate audio process handles the driver and the Lab can stop it and restore the original preset if the driver stops responding.
- **Live guitar** routes the temporary copy to the guitar input and monitor outputs. The guitar stays on the Quad Cortex signal path while the knobs adjust its settings over USB. **Stop live** recalls the original saved preset and scene.
- **Ask Tone Lab** uses the authenticated Codex client for open-ended requests and searches the web for a new rig when appropriate. It sends text context including guitar choice and short take labels. Guitar audio is processed locally. [Windows voice typing](https://support.microsoft.com/en-us/accessibility/windows/use-voice-typing-to-talk-instead-of-type-on-your-pc)

## First hardware session

1. Choose a song section and enter the guitar and pickup. In your normal playing preset, record a short mono DI or import a clean mono WAV. With the factory USB channel mapping, DI channel 1 is physical Input 1, and channel 2 is physical Input 2. Check the Quad Cortex Dry/Wet Swap setting if the DI sounds processed. You can also record in Studio Pro and export the DI. [Neural DSP USB audio guide](https://neuraldsp.com/getting-started/using-quad-cortex-as-an-audio-interface)
2. In Cortex Control, save a new preset into an empty user slot with a name starting with `Tone Lab `, such as `Tone Lab Clean`. Use a single unsplit grid row from **USB 5/6** to **USB 5/6**. Leave the other rows unused. Start with a bypassed chain to verify the signal returns. Add an amp and cab and set up scenes A and B for comparison. The test chain returns audio to the computer over USB and keeps it off the monitor outputs. Use a separate normal playing preset for tracking your guitar.
3. Load the saved test preset and close Cortex Control and any DAW holding the driver. In Tone Lab, pick the dry WAV, optionally pick an isolated reference excerpt, then click **Reamp scenes**. The app checks the test preset and routing before sending anything. It replays the DI from computer output 5, records computer inputs 5/6, and restores the starting scene. A single scene letter works for checking the return path.
4. Use **Play A/B/C** and **Keep last played** when you are ready to audition. Start at a comfortable monitoring level. Open the session folder to see the raw and audition WAVs, `session.json` with measurements and settings, and `winner.json` after choosing a preference. For a playable QC preset, apply the winning settings in a copy with the normal guitar input and monitor outputs and save it with Cortex Control.

For multi-row presets, you can reamp in Studio Pro and use **Compare WAVs** to get the same offline audition. Use the same DI and comparable time windows across exports. The similarity score measures broad spectral shape. Listening still matters for saturation, dynamics, effects and feel. Clipped or inaudible renders are retained for diagnosis and omitted from auditions.

## Text and agent controls

The GUI and CLI use the same recording and comparison code. From the project folder in PowerShell, use the following commands. The examples assume the fixture name and block indices have been checked using `inspect`.

```powershell
$tone = '.\.venv\Scripts\python.exe'
& $tone -m tone_lab latest-clip --pair
& $tone -m tone_lab doctor
& $tone -m tone_lab inspect
& $tone -m tone_lab record .\charvel-bridge.wav --seconds 10 --input 1
& $tone -m tone_lab reamp .\charvel-bridge.wav --guitar Charvel --pickup Bridge --target 'Song, chorus riff' --scenes AB --arm
& $tone -m tone_lab sweep .\charvel-bridge.wav --guitar Charvel --pickup Bridge --column 1 --parameter 1 --values 4.0 4.5 5.0 --arm
& $tone -m tone_lab choose .\tone_sessions\SESSION B --notes 'Tighter low end'
& $tone -m tone_lab apply-winner .\tone_sessions\SESSION --arm
```

`sweep` takes 2–7 requested display values for a single continuous parameter shared across all scenes. The app checks the model and readback, limits values around the original setting and restores the knob after the sweep. `apply-winner` reapplies the winning scene or parameter to the original working test grid. Save a playable copy from the editor. Hardware writes and USB reamp commands require explicit `--arm` and an eligible test preset.

Use these commands to inspect old tones and shortlist listening references locally.

```powershell
$pod = "$env:USERPROFILE\Documents\Line 6\POD Go\Backups\POD Go Backup 2025-Mar-04 after upgrade.pgb"
& $tone -m tone_lab pod $pod
& $tone -m tone_lab pod $pod --user-slot 02B
& $tone -m tone_lab history .\StreamingHistory_music_0.json
```

The archive command reads preset and snapshot settings locally. The history command accepts the standard and extended Spotify audio-history formats and reports track titles by listening time. [POD Go archive format](https://github.com/john-baxter-dev/fretwire/blob/master/docs/pod-go.md) · [Spotify data export](https://support.spotify.com/us/article/understanding-your-data/)

## Installation and verification

The local environment is already installed on this machine. Recreate it on Windows with Python 3.13 and Git using the commands below.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m tone_lab doctor
```

Paired dry/wet recording, USB reamping and direct parameter changes were checked on CorOS 4.1.1. Session data and preset snapshots stay in `tone_sessions` outside Git. Use `python -m tone_lab presets` to list local snapshots and `python -m tone_lab snapshot` to archive the current working state. Snapshot replay is not yet automated. The desktop tools log launch attempts in `tone_sessions/startup.log` and capture and reamp status in `tone_sessions/tone-lab.log`. `tone_sessions/live-driver.log` collects stack traces if the live worker stalls. Run `python -m tone_lab shortcut` from the local environment to rebuild the Windows launcher and refresh the Start, workspace, and existing Tone Lab taskbar shortcuts.
