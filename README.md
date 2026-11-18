# Tone Lab for Quad Cortex

Use the chat to say what you want to play and how the tone should change. For example, “Les Paul bridge, the chorus riff. Use my last clip and try a tighter low end.” The agent can retrieve the latest clip and use it with the recording and comparison tools below.

Open **Tone Lab.lnk** for the Windows taskbar launcher. Click **Record clip**, wait for **Recording · dry + wet**, play, and click **Stop and save**. One take saves the mono dry DI for reamping and the stereo wet signal from Quad Cortex outputs 1/2 as the baseline. The recorder uses physical guitar input 1 by default and checks the Quad Cortex USB Dry/Wet Swap setting while recording. Keep that setting fixed for the take. Tell the chat what you want to try on the last clip.

## Optional workbench

The workbench is available via `python -m tone_lab ui` in the local environment. It exposes USB diagnostics, routing comparisons, candidate audition controls and session files. Its **Offline demo** builds three simulated candidates without starting playback. Its **Check Quad Cortex** action reads the current preset and opens the ASIO driver without starting a stream. The **Play** controls use the Windows default output.

## Compare and give feedback

The main window shows the latest take separately from its reamps. Use **Older** and **Newer** to move between comparisons of that take. Use the one text box for notes such as “A is closer. B is too dry.” Each note keeps its comparison and A/B/C mapping. The saved take, its renders, and the notes remain separate files.

Three short requests also act like style controls. **More jazz** reduces drive, softens the top end and backs off ambience. **Less delay** lowers mix and feedback. **Darker** softens supported tone, treble and presence controls. The renderer tests two amounts and produces a level-matched A/B/C with your original wet take. Keep Cortex Control closed during a direct USB render. The current hardware fixture is a USB-only copy of the Vai preset at **My Presets → 32G**, with the same preset and scene loaded as the recording. The engine checks the copy against the wet take before using it. Open-ended feedback is saved for the next conversation.

### Live controls and the tone advisor

Open **Live controls** to turn **Jazz**, **Drive**, **Warmth**, and **Space**. Drag on a knob or use its arrow keys. The controls combine their changes against the fixed preset baseline. **Reset knobs** returns the controls to that baseline.

- **Loop recorded take** feeds the same dry passage into the Quad Cortex and monitors through its normal outputs. Start with the Quad Cortex volume low. A separate audio process handles the driver and the Lab can stop it and restore the original preset if the driver stops responding.
- **Live guitar** routes the temporary copy to the guitar input and monitor outputs. The guitar stays on the Quad Cortex signal path while the knobs adjust its settings over USB. **Stop live** recalls the original preset and scene. Both modes use the prepared Vai test copy and need the editor closed.
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

Paired dry/wet recording, USB reamping and direct parameter changes were checked on CorOS 4.1.1. Session data stays in `tone_sessions`. The desktop tools log capture and reamp status in `tone_sessions/tone-lab.log`. `tone_sessions/live-driver.log` collects stack traces if the live worker stalls. Recreate the Windows launcher with `python -m tone_lab shortcut` from the local environment.
