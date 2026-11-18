# Tone Lab for Quad Cortex

Open **Tone Lab.lnk** to go straight to **Live**. Keep Cortex Control closed while using the direct USB controls. Tone Lab uses the loaded saved preset as a starting point and a verified working copy in **My Presets → 32G**. It supports a single physical guitar input feeding Multi Out, including parallel lanes. It archives the original and previous working copies locally before changing the scratch slot.

| Intent | What to use |
| --- | --- |
| Change how the guitar feels under your fingers | **Live guitar** puts the working copy on the Quad Cortex hardware path. Start with Drive, Warmth, Space and Jazz. **More controls** exposes Response, EQ and Effects separately. |
| Compare changes on the exact same performance | **Record clip** captures synchronized dry DI and wet audio. **Loop take** reamps that DI through the currently loaded preset's working copy. **Save A/B** records the current settings against the wet take and adds the render to the library. |
| Describe a sound or research a song | **Ask AI** turns a request into suggested knobs. **Tone Search** finds saved takes and renders by name, notes and audio traits. **Research rig** runs a separate live web search and returns sources. |
| Get closer to a reference | In **Tone Match**, choose a WAV or use the take's wet audio. Set the relative importance of tone, dynamics and ambience, and audition the closest saved results at matched loudness. The scores describe similarity within the library. Build or render more candidates when the library has no close match. |
| Say what worked | Write a note in **Feedback**. The note stays attached to its comparison and letter mapping. |

## Guitar input experiment

**Guitar input** is separate from the default Live view. Train one profile per instrument and control, using at least two labeled examples. For a first pickup profile, record the same single-note phrase at neck and bridge with the guitar volume, tone, picking point, and input gain held steady. **Inspect** shows the state estimates and uncertainty on a fresh DI. Start with a take containing ordinary playing and a second take containing an intentional gesture. Pickup location and picking point both change the harmonics. Volume inference also depends on how hard you play. [Pickup and plucking-point estimation study](https://qmro.qmul.ac.uk/xmlui/bitstream/handle/123456789/31405/Dixon%20Pickup%20position%20and%20plucking%202017%20Published.pdf?isAllowed=y&sequence=1)

To use the guitar as a controller, choose a starting state, an excursion, and a tone control. Rehearse A → B → A with a sustained note or repeated notes at each position. A silent switch gives the estimator no note to compare. **Observe during Live guitar** monitors dry USB input in memory. **Arm mapped control for the next run** permits bounded changes for the first 20 seconds. A return gesture changes one virtual control. **Stop live** ends the experiment. The same pattern can be tested on calibrated tone-knob positions. Observation and arming are off on app launch.

The tone-pot compensation experiment runs offline. Record the same phrase with the same notes in each half of the clip, once with the tone open and once rolled back. `python -m tone_lab test-compensation open.wav rolled.wav compensated.wav` tries a shelf capped at +6 dB and applies it to a new WAV only when it improves the held-out half. This measures an approximate correction before auditioning the effect chain. It does not reconstruct missing harmonics or compensate live.

## Sound check

- **Input and output** measures the recorded DI peak, wet loudness, estimated true peak, and samples near full scale. Play the hardest attack you expect at the gig when making the check take. USB measurements describe the captured signal. Check the Quad Cortex input meter and the mixer separately for headroom along the live path. [Quad Cortex manual](https://neuraldsp.com/manual/quad-cortex) · [Yamaha gain staging](https://hub.yamaha.com/proaudio/livesound/gain-staging/)
- **Relative preset levels** measures raw reamps of the same phrase and proposes a post-chain trim. The default plan uses the quieter usable render as an anchor. For an intentional lift, `python -m tone_lab levels --comparison <folder> --reference A --offset B=2` plans B two dB above A while leaving peak headroom. The plan measures the recorded excerpt. A band soundcheck is the way to judge whether a lead actually comes forward. [ITU-R loudness and true-peak measurement](https://www.itu.int/rec/R-REC-BS.1770-5-202311-I)
- **Stage EQ preview** makes matched-level variants with a low cut and a gentler top end as separate choices. Listen against the bass, drums, and vocal before choosing either. Use pre-drive EQ to change how the amp saturates, post-chain EQ to shape the finished guitar tone, and output-specific Global EQ for a shared speaker or venue correction. The previews and trim plans leave the Quad Cortex unchanged. [Yamaha band PA guide](https://usa.yamaha.com/audio/portable-pa/explore/pa-beginners-guide/setting/band-pa.html) · [Quad Cortex Global EQ](https://neuraldsp.com/manual/quad-cortex)

## Optional workbench

The workbench is available via `python -m tone_lab ui` in the local environment. It exposes USB diagnostics, routing comparisons, candidate audition controls and session files. Its **Offline demo** builds three simulated candidates without starting playback. Its **Check Quad Cortex** action reads the current preset and opens the ASIO driver without starting a stream. The **Play** controls use the Windows default output.

## Compare and give feedback

The main window shows the latest take separately from its reamps. Use **Older** and **Newer** to move between comparisons of that take. Use the one text box for notes such as “A is closer. B is too dry.” Each note keeps its comparison and A/B/C mapping. The saved take, its renders, and the notes remain separate files.

Three short requests also act like style controls. **More jazz** reduces drive, softens the top end and backs off ambience. **Less delay** lowers mix and feedback. **Darker** softens supported tone, treble and presence controls. With the take's recorded preset and scene loaded, these requests produce two variations against the original wet track. Open-ended feedback is saved for the next conversation.

### Live controls and the tone advisor

Drag, scroll, or use arrow keys to turn a knob. The controls combine their changes against the fixed preset baseline. **Reset** returns to that baseline. Drive at its minimum backs off gain and bypasses compatible active drive pedals. A modeled capture can retain the distortion of its source. Choose a clean model or capture when you want a genuinely clean starting point.

- **Loop recorded take** feeds the same dry passage into the Quad Cortex and monitors through its normal outputs. Start with the Quad Cortex volume low. A separate audio process handles the driver and the Lab can stop it and restore the original preset if the driver stops responding.
- **Live guitar** routes the temporary copy to the guitar input and monitor outputs. The guitar stays on the Quad Cortex signal path while the knobs adjust its settings over USB. **Stop live** recalls the original saved preset and scene.
- In **Tone advisor**, dictate into the text box with Windows **Win+H** or type. **Ask AI** uses the authenticated Codex client and sends the request, knob positions and short take labels. **Speak reply** uses the Windows voice. **Use suggested knobs** applies proposed settings, including during a live loop. The Lab sends text to Codex and keeps guitar WAVs local. [Windows voice typing](https://support.microsoft.com/en-us/accessibility/windows/use-voice-typing-to-talk-instead-of-type-on-your-pc)

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
