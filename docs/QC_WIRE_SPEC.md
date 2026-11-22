# Quad Cortex USB API specification

This describes the client-visible USB control protocol on Quad Cortex. It combines the pinned SDK's recovered schemas with USBPcap observations from CorOS 4.1.1 and Cortex Control 4.1.0 on Windows. A descriptor establishes how a message is encoded. A trace establishes what the editor sent and how the unit answered in that test. A successful UI edit and echo establish a reversible operation on this version. Persistence across reboot and behavior on other firmware require separate evidence.

## Machine-readable schema and traces

```powershell
$lab = '.\.venv\Scripts\python.exe'
& $lab -m lab qc-api wire-spec --schema 4.1
& $lab -m lab qc-api wire-spec --schema 4.0
& $lab -m lab qc-api wire-trace <capture.pcap> --device <USB_ADDRESS> --tshark 'C:\Program Files\Wireshark\tshark.exe'
& $lab -m lab qc-api settings
```

`wire-spec` includes the numeric command IDs, nested messages, field numbers and types, field presence and repetition, oneof membership, and numeric enum values. The recovered 4.1 schema has 73 command names, 151 reachable messages, and 32 enums. The 4.0 schema has 71 command names, 144 reachable messages, and 30 enums. Command 0, `Undefined`, has a name but no message descriptor.

`wire-trace` takes a saved Windows USBPcap file and emits one JSON line per reconstructed message. It includes direction, first and last frame, epoch time, command ID, decode status, field presence and any explicit action or request ID. `--values` also includes decoded payload values. Keep that output and the PCAP in the private Tone Lab data folder or git-ignored `personal/`. The decoder can recover known commands even when an opaque payload does not parse as its named protobuf.

The input reports observed here contain the report ID and 128 data bytes. ID 2 is host-to-device and ID 1 is device-to-host. Each payload begins with a chunk length and flags. Keep independent reassembly state per report direction. The logical message ends in an eight-byte little-endian trailer containing a 16-bit command, 32 reserved bits, and a 16-bit hash. The protobuf body can be gzip-compressed. TShark exposes the HID report data separately from the USB control setup bytes. The device report arrived on interrupt endpoint `0x81`, and editor writes used USB control endpoint `0x00`. [SDK protocol reference](https://github.com/lexasoft123/qc-mcp/blob/main/PROTOCOL.md) · [USBPcap transfer types](https://desowin.org/usbpcap/capture_limitations.html)

## Verified settings and performance control

| Editor control | Wire operation | Observed behavior |
| --- | --- | --- |
| Device Settings → Brightness → Screen | Command 9 `GeneralSettings.UPDATE`, field 4 `screen_brightness` | Display values 5 and 7 corresponded to wire values 15 and 21. This establishes three wire units per UI step between those values. Full range and rounding remain unmeasured. |
| Device Settings → Scene Bypass State Behavior | Command 9 `GeneralSettings.UPDATE`, field 9 `scene_block_bypass` | `0 ALWAYS_OVERWRITE`, `1 NONSTOMP_OVERWRITE`, `2 NEVER_OVERWRITE`. Each UI selection yielded a matching device reply. |
| Device Settings → MIDI Settings → Ignore duplicate PC | Command 9 `GeneralSettings.UPDATE`, field 12 `ignore_duplicate_pc` | Explicit Boolean `true` and `false` both echoed. This panel's change used command 9. Command 8, `MIDISettings`, was absent from the first capture and appeared when editing the preset's MIDI Out messages in the second recording. |
| Device Settings → Hold Timing | Command 9 `GeneralSettings.UPDATE`, field 39 `hold_timing` | The UI displayed a 500–1,000 ms range. The two tested settings, 700 and 800 ms, sent ordinals 2 and 3 and got matching echoes. |
| Device Settings → Dynamic Latency Compensation | Command 9 `GeneralSettings.UPDATE`, field 27 `enable_dynamic_delay_compensation` | Disabling and restoring sent explicit Booleans with matching replies. |
| Device Settings → Global Bypass | Command 9 `GeneralSettings.UPDATE`, fields 7 and 8 `global_bypass_cab` and `global_bypass_ir` | Two independent nested messages carry row Boolean flags. The cab sequence was row 2, rows 2 and 4, row 4, then an explicitly present empty message. IR row 3 was enabled and restored separately. Each update echoed the same nested value. |
| Scene strip | Command 13 `Scene.UPDATE`, field 3 `selected_scene` | B sent 1 and A sent 0. The corresponding replies echoed those values. |
| Startup settings | Command 9 `GeneralSettings.READ` | Cortex Control's initial request carried no request ID. The unit returned a full UPDATE with no request ID. In a later exclusive session an explicit request ID worked and returned the settings after restoration. |
| I/O routing and Global EQ | Commands 3 `IOSettings.READ` and 38 `GlobalEQ.READ` | Seen at startup. The replies included nested I/O settings and Global EQ parameters and bypass. Opening the settings panels alone did not establish write behavior. |
| Master level, mode and tempo | Commands 17 `MasterVolume.READ`, 14 `Mode.READ`, 33 `GlobalTempo.READ` | Seen at startup. These also work through the exclusive read interface. Global tempo continued arriving as telemetry. |
| CPU load and I/O metering | Commands 26 `CPULoad`, 5 `IOMeter`, 37 `GridModelMeter` | The editor sent subscription-shaped requests with the default action omitted and explicit DELETE messages when scope changed. CPU and I/O produced unsolicited UPDATE traffic. This explains why a generic correlated CPU READ can time out. |
| Tuner | Commands 27 `ShowTuner` and 6 `Tuner` | Open sent `ShowTuner.UPDATE` with `show=true`, then `Tuner.UPDATE` with `enable_meter=true` and a separate settings READ. The read returned source, frequency, and mute. Closing omitted `show` and explicitly disabled the meter. |
| Gig View | Command 24 `ShowGigView.UPDATE` | Opening set `show=true` and closing omitted it. Both replies matched the request ID. |

Known settings edits above used sparse UPDATE messages. In the trace, each update included its own request ID and the edited field. A brightness edit also led to extra replies for dimmed-LED mode fields carrying the same ID. Match both request ID and expected payload fields. Do not treat the first reply for that ID as a complete snapshot. Startup traffic and telemetry can carry no request ID.

Other `GeneralSettings` fields include LED brightness, MIDI channel/clock/USB, row-wise cab and IR bypass, hold timing, dynamic delay compensation, looper assignments, master volume assignment, and storage information. The schemas expose their encodings, and many appear in a full read. Keep read and write evidence separate. `qc-api settings` and MCP `get_settings` expose the full `GeneralSettings` read alongside the existing I/O, Global EQ, master volume, tempo, and mode reads.

## Settings schema changes between 4.0 and 4.1

| Wire family | Recovered descriptor difference |
| --- | --- |
| General settings | 4.1 removes the cloud endpoint at field 20 and adds external clock tempo and out-of-range fields at 40 and 41. |
| I/O | 4.1 adds a nested preset-selection field at 6. |
| Global EQ | Field 5 changes from a Boolean to a nested model-preset selector. Field 6 is present only in 4.0. Use the firmware-specific descriptor before interpreting or constructing this payload. |
| Version | 4.1 adds a cloud endpoint field at 19. |
| New command families | 4.1 adds command 71 `ModelPreset` and 72 `RemoteControl`. |

## Session, preset state and inventory

| Operation | Evidence from the startup capture |
| --- | --- |
| Establish a session | `ResetCommsBuffers` sent a request ID and session ID. The device echoed the session ID. `Connection` carried the connected flag. Regular `KeepAlive.UPDATE` carried `is_online`. |
| Read the preset pointer, contents, and edit state | The editor sent `SetlistPosition.READ`, `RecallPreset.READ`, `PresetDirty.READ`, and `Scene.READ`. Replies included the folder, slot and factory flag, the nested preset and reason, the edit-state message, and the selected scene. An empty edit-state payload apart from the action was observed for a clean preset. |
| Enumerate files | A `File.READ` carried `omit_factory_content`. `File.UPDATE` replies included a content estimate and typed folder messages. The device streamed hundreds of replies from one request. |
| Recent and favorite items | `RecentsFavorites.READ` used item type and the favorite flag to select a list. The reply included items and echoed those selectors. |
| Model catalog and model presets | `ModelRepo.READ` returned `model_repo_payload`. `ModelPreset.READ` and another request carrying a loaded model hash returned model preset entries and loaded selection metadata. |
| Editor state and gatekeeping | The editor requested looper, undo history and Gig View state during startup. The trace establishes which families answer during initialization. Their editor controls and state transitions still need individual labels. |

## Captured preset editing and persistence

The second recording used one elevated worker for the entire batch. It reconstructed 7,771 messages, including 1,032 host-to-device messages across 42 distinct command IDs. A disposable preset in an empty Tone Lab slot held the edited graph. The input was disconnected before editing, the settings were restored, and the original factory preset and scene were read back after the editor closed.

| Editor action | Request and observed behavior |
| --- | --- |
| Recall empty, saved, or factory preset | `SetlistPosition.UPDATE` carried the folder key, zero-based slot, and factory flag. |
| Disconnect input | `Grid.UPDATE` carried `preset.chains[].in_portid=0` and the row. The unit returned targeted updates and then reported the preset dirty. |
| Add amp or cab | `Grid.UPDATE` carried the model hash and zero-based column inside the targeted row. The add generated multiple messages under one request ID. |
| Assign Gain to scenes | A sparse `Grid.UPDATE` set `params[].scene_mode=true` for index 0 of the test amp. After selecting scene B, a second update sent `param_values[].float_value` of about 0.66768. The UI showed 6.7 in B and 5.0 in A, including after saving and recalling the preset. |
| Move a block | Command 12 `GridMove` carried a `move[]` record with source and destination coordinates and `is_drop=true`, plus the pre-move grid's model IDs. The unit echoed `move[]` without a request ID. |
| Create a split and mixer | Dragging a block to row 2 also used `GridMove`. The requested source and destination plus the prior grid were enough for this editor action to create the split and mixer. The later saved preset contained split controls and mixer data. [Quad Cortex manual](https://neuraldsp.com/manual/quad-cortex) |
| Change mixer level | `Grid.UPDATE` addressed `preset.chains[].mixer[].params[]`, with the row, parameter index 0, and normalized float. In this test 0.62128174 was shown as −7.7 dB for Level A. The unit echoed the same float. |
| Toggle block bypass | `Grid.UPDATE` used `preset.bypass[].colBypass[].sceneBypass[]`. The bypassed state used `bypass=true`, and re-enabling the block sent an empty scene entry. |
| Bind a local IR to a stock cab | The tested stock cab's first mic selector used a sparse string update at parameter index 1. Its value was the IR key beginning with `CIR_`, with no display name. This is a different path from the dedicated IR Loader. |
| Assign expression pedal | The Bass parameter used a sparse update with `expression=1`, `expression_min=0.0`, and `expression_max=1.0`. The pedal assignment stayed visible after recall. Physical pedal movement was outside this test. |
| Assign a stomp switch | `Grid.UPDATE` carried `preset.stomp_mode_assignments[]` with column, zero-based stomp index 3 for D, and `type=PRIMARY`. The unit echoed the update. |
| Edit preset tempo | `GlobalTempo.UPDATE` used parameter index 0. The preset-tempo UI mapped 120 BPM to 0.400 and 121 BPM to 0.405 in this test. The update carried no request ID, and the preset dirty flag tracked the change and restoration. |
| Configure preset MIDI Out | Command 8 `MIDISettings.UPDATE` carried `general_midi_messages.messages[]` with source 0 for footswitch A and a list of message slots. CC used type 1 and channel 1 as displayed. Returning to None sent empty message slots. Both changes echoed. No physical transmission or preset-load MIDI was tested. |
| Save into an empty slot and save it again | Command 4 `File` carried the destination folder and index with the default zero action omitted. The first save supplied the name. Saving the same slot again omitted it and yielded an additional device-side DELETE notification for the preceding file entry, followed by a replacement entry. Both saves returned the full recalled preset and new position with the same request ID. A later recall confirmed the stored graph and scene values. |

## Coverage and collecting a single batch

The first device-address-filtered capture reconstructed 4,153 messages across both directions, including 37 distinct host-to-device command IDs. Several cloud, license and forwarding payloads were opaque under the recovered descriptors. Command names on those messages identify a family only.

For a new bench, find the current Quad Cortex address in USBPcap's device tree. Use USBPcap's device selector and start one bounded elevated capture before opening Cortex Control. Use one idle interval and a timestamp log. Change one control at a time, confirm its reply, restore the value, and move to the next item on the same recording. Keep the original preset and scene recorded separately. Use a Tone Lab slot for signal-chain edits. Give the recorder a deadline and let the same elevated worker stop its own capture. Run all decoding and comparison as the normal user. TShark can replay the same file repeatedly. [TShark manual](https://www.wireshark.org/docs/man-pages/tshark.html)

Remaining evidence gaps include standalone IR and capture file transfers, whole-backup containers, physical pedal movement, MIDI transmission, and looper record/play. Verify global gain, routing and phantom-power writes separately with safe monitoring. Leave account changes, firmware updates, factory reset and whole-device restore in the official workflow. The recordings provide no evidence for those operations' implementation or safety.
