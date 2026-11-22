# Quad Cortex API discovery results

Tone Lab now exposes a read-only discovery API in [`lab/qc/discovery.py`](../lab/qc/discovery.py), a Windows CLI, and an optional local MCP server. The hardware checks below used CorOS 4.1.1, Cortex Control 4.1.0, and the pinned `qc-mcp` commit in [requirements.txt](../requirements.txt).

## Use it

Run from the repository root. Offline commands work while the desk is open. For live CLI reads, close Tone Lab first so the desk can release its exclusive device handle.

```powershell
.\.venv\Scripts\python.exe -m lab qc-api capabilities
.\.venv\Scripts\python.exe -m lab qc-api catalog
.\.venv\Scripts\python.exe -m lab qc-api snapshot
.\.venv\Scripts\python.exe -m lab qc-api watch --share-editor --count 10
.\.venv\Scripts\python.exe -m lab qc-api settings
.\.venv\Scripts\python.exe -m lab qc-api inventory
```

With Cortex Control closed, `watch` emits JSON lines with a baseline, field-level changes, and stale reads. A successful read after a stale interval starts a new baseline. A snapshot includes the live chain, slot, scene, edit flag and a content fingerprint. It checks slot, scene and edit flag before and after reading the chain. The fingerprint supports comparisons and previews, and provides no hardware lock.

With Cortex Control open, `watch --share-editor` listens to incoming HID traffic and reports message types and counts per window. It sends zero commands, including during open and close. A fresh window means traffic arrived. It does not imply that the current preset was read.

Use `inventory --details` locally for preset, IR and capture names and keys, including a list of deduplicated local assets. Snapshots and detailed inventories can contain private data. Keep redirected output under the Tone Lab data folder or git-ignored `personal/`. Use `diff BEFORE.json AFTER.json` to compare snapshots, `preview SNAPSHOT.json ROW COLUMN PARAMETER VALUE` to preview a single numerical control in display units, and `preset-file FILE.pb --schema 4.1` to inspect an exported BinaryPreset copy. Snapshots and decoded preset files report recognized IR Loader and Neural Capture references by key.

An MCP client can start `.\.venv\Scripts\python.exe -m lab qc-api mcp` over stdio. Its resources are `qc://capabilities` and `qc://models`. Its tools are `get_snapshot`, `get_settings` and `preview_parameter`. Add `--simulated` to use the fake device, which also works with the other live discovery commands. Existing app actions for building, auditioning and reamping still use the guarded engine.

## Evidence

| Surface | Result |
| --- | --- |
| Offline protocol | 73 command names and 72 with a message descriptor. Also found 54 public SDK methods and 60 registered tools in the pinned MCP source. Those are separate surfaces, and the tool count measures declarations. The upstream default branch resolved to the pinned commit when checked. |
| Editor coexistence | A second Windows HID handle received Grid, I/O meter, global tempo and CPU load traffic. The passive watcher received traffic for six consecutive two-second windows with the editor still running at the end. Active position, scene, edit-state and full preset reads worked in some parallel runs. Other runs timed out, and Cortex Control exited unexpectedly during one repeat. The cause of the exit was unverified. The shipped shared watcher uses only the passive handle, and full snapshots take an exclusive handle with the editor closed. |
| Global settings | Correlated reads returned mode, tempo, I/O settings, Global EQ and master volume. The same generic read timed out for MIDI settings and CPU load. CPU load was observed in the passive stream and needs a separate subscription or event path. |
| Asset directory | The unit listed presets, IRs and captures. Existing code resolves local assets by key and keeps a private library. Directory entries can include folder links and factory or cloud items. [Asset decoding](../lab/qc/assets.py) |
| Guarded edits | A synthetic Gain chain built in the verified scratch/cache path with inputs disconnected. Scene B read back in 0.20 s. A gain change produced a semantic parameter diff and a different fingerprint in a 1.43 s edit/read cycle. Reopening the slot gave back the saved chain. The original slot and scene were verified after closing the session. |
| USB audio | Calibration returned a 5,350-sample USB round trip at 48 kHz, about 111 ms. The ordinary engine reamped a synthetic signal and returned its measurements. The original slot and scene were verified again. This check used USB rather than a physical input and provided no simultaneous live-monitoring evidence. |
| Export path | The offline decoder accepts a versioned BinaryPreset protobuf. A synthetic copy was read with its bytes preserved. Whole Cortex Control backup containers remain a separate format. |

The legacy `inspect` CLI initially reproduced a native PortAudio shutdown crash. Deferring ASIO initialization to the first audio operation let `inspect` finish without loading the driver. Explicit calibration and synthetic reamping also completed separately.

## Coverage still open

- Cross-check the watcher against labeled changes made in Cortex Control and on the front panel, including IR selection and unsaved graph edits. The shared bench proved observation and disconnect handling. Its phase did not automate edits in the official editor.
- Confirm expected event subscriptions and direct reads for MIDI and expression assignments, CPU, tuner and looper. Catalog schemas and passive events are early evidence for these families.
- Run an asset round trip on disposable content, compare exports and backup manifests, and verify plugin availability on-device. Inventory and model metadata alone cannot establish these behaviors.
- Extend hardware evidence to scene-specific assignments, split/mix routing, playback during manual changes, and an explicit saved-copy workflow. The first write check used the existing exclusive engine and its silent cache.
- Expose guarded write intents to the CLI or agents after testing the handoff against real intervening edits. The preview fingerprint does not authorize a write. Cortex Control and Tone Lab still take turns when the app needs to write, and the CLI and MCP currently require the desk to release the device before observing it.

The complete implementation and promotion criteria are in the [discovery plan](QC_API_PLAN.md). Raw traces from this run are private.
