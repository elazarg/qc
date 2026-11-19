# Reader's report: latency, responsiveness and failure modes

One of nine reports behind `REWRITE_REVIEW.md`, written by a reader that read the code and the logs on 2026-11-19. Its claims were not all re-checked. Line numbers are as of commit `11f5382`. Paths are under `tone_lab/`; `transport.py` and `winhid.py` are in `.venv/Lib/site-packages/qc_mcp/`.

Times are computed from the code unless marked logged or measured. Q is one state read from the unit, a fixed 0.1 s: the transport sleeps its whole poll window (`winhid.py:448`, `transport.py:443`).

## 1. Time budgets

- **Connect** (`device.py:39-58`): `tasklist` twice, 1.15 s of fixed handshake windows (`transport.py:319-357`), +1.5 s when read-only (`transport.py:121`), 0.2 s on close (`transport.py:246`).
- **App start**: 2-5 s to window (logged), then `read_qc` (`listening_room.py:2057`), 2.85 s fixed. Repeats on window focus (`:235`) and after every stop (`:2050`).
- **Start, saved preset**: spawn, connect, `prepare_fixture` (`iterate.py:107-156`): one recall (0.25 s, `iterate.py:53`) if the copy matches, else four, plus 2×0.55 s after writes and 0.45 s routing (`iterate.py:86-87`); input connect 0.45 s. Sleeps: about 2 s warm, 4 s cold. Logged 7.5-15 s (path unverified).
- **Start, draft** adds `select_draft` (`monitor.py:243-303`): two recalls; build (`draft_render.py:294-356`) 0.07 s per deleted block, 0.25, per block 0.10 + Q + 0.07 per setting, 0.40, four whole-preset read-backs; store 0.55 s. Sleeps about 7 s (7 blocks assumed). Logged 16.2-19.8 s on ten starts.
- **Knob**: queues up to 0.125 s (`live_client.py:106`, `monitor.py:73`), guard 2Q (`live.py:20-22`), 0.012 s per parameter (`live.py:28`), no read-back. About 0.25-0.5 s, no gap.
- **A/B flip**: sleeps 0.70 s, about 1.5 s of Q, three preset read-backs (`monitor.py:260-265`, `453-473`, `92-97`). The measured 0.95 s "connect" is 0.45 s of sleeps plus 0.5 s of reads. Silent throughout.
- **Step**: full rebuild, 17 s silent. One copy per side (`monitor.py:258`, `299`), so stepping back rebuilds.
- **New sound**: deadlines 180/240 s per round (`preset_design.py:737`), 50/85 s single (`advisor.py:166`). On arrival while live, design 1 replaces B: 17 s of silence (`suggestions.py:88`, `122`).
- **Save**: refused while live (`listening_room.py:1946`). Then two directory listings (`draft_render.py:546`, `553`), a third build (`:548`), 0.40 + 0.65 s, two recalls. Sleeps about 10 s; total never measured (`UX_REVIEW.md:61`).
- **Stop**: restore recall (`monitor.py:305-314`), 0.2 s close, then `read_qc` again. Ignored during a build (`monitor.py:289`; `live_client.py:84` waits 45 s).

Known necessities: the 0.2 s mute per block and the position guard. Every settle sleep (0.05-0.7 s) is an uncommented guess.

## 2. UI thread

Every `result()` sits behind `done()` (`listening_room.py:2100`, `2136`; `suggestions.py:229`). Tk-thread I/O: state and round files written on each knob step (`listening_room.py:727-744`, `1034`; `suggestions.py:153-162`); a 5.3 MB JSON parse at start and after each save (`qc_browser.py:38`, `109`). The real stall is the single device worker (`listening_room.py:160`): a focus-triggered `read_qc` delays Guitar by 3-6 s. The player sees one unchanging "Preparing" line for 17-20 s.

## 3. Failures in the logs

- **Worker stopped responding**: 3 recoveries, 2 tracebacks, all on 18 November 02:18-02:23, waiting in `get_setlist_position` (`live-driver.log:27-33`). Preset recalled 6 s later each time; restart worked.
- **Preset or scene changed**: 2 (14:25, 14:51), each two minutes into Guitar on a draft. Session ended, error shown 6 s later, unit left on the player's selection (`live_worker.py:27`). Restart cost a 17 s rebuild.
- **AI timeout** 1 (85 s); Codex missing 1; validation error 1. No unit effect.
- No ASIO dropouts, unplug or ASIO-busy events logged.

Raise sites: `draft_render.py:101`, `238`, `299`, `367`, `410`, `556`; `monitor.py:94`, `102`, `382`, `463`; `live.py:21`, `23`, `137`; `hearing.py:126`; `iterate.py:123`. The guard is necessary; ending the session is not, since nothing was written to the new preset. Stop writing, keep the session, offer "Return to B" (a recall) or "Shape this one". A scene change, which `monitor.py:381` conflates, should be followed.

## 4. Recovery

- **Unplug**: writes fail silently (`transport.py:377-386`), the next guard raises, `--recover` fails, and the marker is never replayed at startup (only callers `live_client.py:144`, `rounds.py:353`). The unit stays on a USB-routed slot: silent guitar; Shape refuses to start (`monitor.py:190`).
- **Cortex Control**: checked only at connect; controls pause until a focus event (`listening_room.py:2112-2115`).
- **Hang**: 18 s start, 6 s heartbeat, 45 s load (`live_client.py:14-17`), then kill and a 12 s recovery. A long first build could trip 45 s (unverified).
- **ASIO busy**: Phrase falls back to Guitar; session survives (`monitor.py:126-136`).
- **LLM**: retry offered; the automatic next round cannot be cancelled (`suggestions.py:221`).
- **Stale**: an old `qc_position` makes listening report "busy" every 30 s, silently (`suggestions.py:312-320`).

## 5. Slow or fragile by design

One connection and one process per operation. Built copies live in worker memory, one per side, so a design is built for listening, again for playing, again per restart, again for Save. Only loudness is cached (`live_levels.py:40-61`). A 0.8 s poll costs 0.2 s (`monitor.py:545`). Listening takes the unit 4 s after Stop, checks for playing once (`rounds.py:121-127`) and leaves the guitar silent.

## 6. Proposals

1. **Slot cache keyed by content hash**, filled when the app listens: start, step and save become a recall or copy. 17-20 s to 1-3 s.
2. **A round, and A/B, as rows of one preset**: 2-3 s silent to 20 ms. Knob remedies as scenes: no gap. CPU fit unverified.
3. **One long-lived session with pushed state**: saves 1.35-2.85 s per operation and the 6 s re-reads.
4. **Event-driven acknowledgement instead of sleeps**: flip under 1 s, knob under 0.1 s, build about 5 s shorter (estimates).
5. **Adopt or return on preset change**: saves about 25 s per incident.
6. **Cancellable builds; Stop as a recall**: under 1 s.
7. **Never replace B while live; three parallel design calls** (54 to about 22 s, unverified).
8. **Replay the recovery marker at startup; per-block progress.**
