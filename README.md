# Tone Lab for Quad Cortex

Open **Tone Lab.lnk** to go to the listening desk. Pick the guitar you’re playing and describe a sound in **Ask Tone Lab**. Press **Ctrl+K** to jump to the request box or use **Win+H** to dictate.

- Click **A** or **B** to play or change the sound. When both come from the same phrase, the loop stays at the same place and levels are matched. **Esc** stops playback. **Keep this sound** stores a local listening decision.
- Ask “Make me a Lenny Kravitz preset.” **B** renders the draft on your current phrase. **Try with guitar** auditions it temporarily on the Quad Cortex. **Details** shows the reasoning and sources and can **Save a copy to My Presets**.
- Choose an audio excerpt, a previously captured reference, or a saved Quad Cortex preset. Capturing a fresh preset reference replays your phrase through it and restores the starting device preset and scene. **Find a match** brings a saved alternative for the current phrase into the listening comparison.
- Use **Tools → Open Cortex Control** for detailed hardware editing. The Lab leaves device control to Cortex Control while it is open. Your session, drafts, saved references and playback remain available. Capturing or saving with direct hardware control requires a handoff. Audio-only recording can retain four raw channels while the editor is open; the preset identity is provisional.
- **Tools → Open workbench** opens the earlier controls for live macros, calibration, levels and detailed tone search. **Back to listening** returns to the same session.

The Live controls and preset references use **My Presets → 32G** as a working copy. Draft auditions temporarily use **32H**. Tone Lab checks the working slot, archives the starting preset locally, and restores the original saved preset and scene when the audition ends. **Save on QC** is the action that keeps a draft in a regular My Presets slot.

Rig sources appear under **Details** when the advisor finds them. A named artist gives the draft a musical target. The recorded take provides the performance for auditioning. To plan the next iterations, see [Tone Lab product direction](INTENT_AND_LANDSCAPE.md).

## Guitar input experiment

Open **Guitar input**, choose the guitar, and start with **Pickup selector**. Use **Record example**, play a short repeating phrase on one pickup, stop, and label it. Repeat the same phrase on a second pickup with volume, tone, picking point, and input gain held steady. The view shows the collected settings and suggests the next one. Train the profile, then check it with a fresh dry clip. Pickup location and picking point both change harmonics, and volume inference also depends on how hard you play. [Pickup and plucking-point estimation study](https://qmro.qmul.ac.uk/xmlui/bitstream/handle/123456789/31405/Dixon%20Pickup%20position%20and%20plucking%202017%20Published.pdf?isAllowed=y&sequence=1)

To use the guitar as a controller, choose a starting state, an excursion, and a tone control. Rehearse A → B → A with a sustained note or repeated notes at each position. A silent switch gives the estimator no note to compare. **Observe during Live guitar** monitors dry USB input in memory. **Arm mapped control for the next run** permits bounded changes for the first 20 seconds. A return gesture changes one virtual control. **Stop live** ends the experiment. The same pattern can be tested on calibrated tone-knob positions. Observation and arming are off on app launch.

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

Paired dry/wet recording, USB reamping and direct parameter changes were checked on CorOS 4.1.1. Session data and preset snapshots stay in `tone_sessions` outside Git. Use `python -m tone_lab presets` to list local snapshots and `python -m tone_lab snapshot` to archive the current working state. Snapshot replay is not yet automated. The desktop tools log capture and reamp status in `tone_sessions/tone-lab.log`. `tone_sessions/live-driver.log` collects stack traces if the live worker stalls. Recreate the Windows launcher with `python -m tone_lab shortcut` from the local environment.
