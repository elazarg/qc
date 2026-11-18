"""A single control for handing a dry and wet guitar take to the chat workflow."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import Event
import tkinter as tk
from uuid import uuid4

import numpy as np

from . import audio, device, diagnostics, session


def clip_paths():
    folder = session.SESSIONS / "recordings"
    clips = [*folder.glob("clip-*.wav"), *folder.glob("clip-*/dry.wav"),
             *(raw for raw in folder.glob("clip-*/inputs.wav") if not raw.with_name("dry.wav").is_file())]
    return sorted(clips, key=lambda clip: clip.stat().st_mtime_ns, reverse=True)


def latest_clip(*, pair=False):
    paths = clip_paths()
    if not paths:
        raise ValueError("There is no clip yet. Open Start Tone Lab and record a riff.")
    return clip_info(paths[0], pair=pair)


def clip_info(path, *, pair=True):
    if path.name == "inputs.wav":
        if not pair:
            raise RuntimeError("The last take was saved as raw USB inputs. Its dry and wet mapping needs to be verified.")
        info = json.loads((path.parent / "clip.json").read_text(encoding="utf-8"))
        return {**info, "raw": str(path), "dry": None, "wet": None}
    if pair:
        if path.name != "dry.wav":
            return {"dry": str(path), "wet": None}
        info = json.loads((path.parent / "clip.json").read_text(encoding="utf-8"))
        return {**info, "dry": str(path), "wet": str(path.with_name("wet.wav"))}
    return path


def infer_editor_pair(samples, input_channel):
    """Identify a pair only when the selected dry input has a quiet neighbour."""
    if input_channel not in (1, 2) or len(samples) < audio.RATE // 2:
        return None
    rms = np.sqrt(np.mean(np.square(samples, dtype=np.float64), axis=0))
    possible = []
    for dry_base, wet_base in ((0, 2), (2, 0)):
        dry = dry_base + input_channel - 1
        idle = dry_base + 2 - input_channel
        useful = min(rms[dry], rms[wet_base], rms[wet_base + 1])
        if useful > .0002 and rms[idle] < useful * .063:
            possible.append((dry + 1, wet_base + 1, wet_base + 2))
    return possible[0] if len(possible) == 1 else None


def record_take(input_channel, guitar, stop, ready):
    """Save one performance and return its folder and recording status."""
    take_id = uuid4().hex[:6]
    diagnostics.LOG.info("Capture %s requested on physical input %d", take_id, input_channel)
    # Capture both USB pairs immediately. Resolve their meaning while the take runs.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-routing") as routing:
        channel_mapping = routing.submit(device.recording_channels, input_channel, include_context=True)
        diagnostics.LOG.info("Capture %s opening ASIO inputs 1–4 at %d Hz", take_id, audio.RATE)
        samples = audio.record_inputs(audio.MAX_SECONDS, (1, 2, 3, 4), stop=stop, ready=ready)
        try:
            channels, context = channel_mapping.result()
        except device.EditorActive:
            channels = infer_editor_pair(samples, input_channel)
            context = {"usb_mapping_source": "inferred-from-signal", "wet_preset_unverified": True}
        except Exception:
            diagnostics.LOG.exception("Capture %s could not verify dry/wet routing", take_id)
            channels = None
    diagnostics.LOG.info("Capture %s received %d frames", take_id, len(samples))
    if len(samples) < audio.RATE // 2:
        raise ValueError("Wait for Recording, then play for at least half a second.")
    folder = session.SESSIONS / "recordings"
    folder.mkdir(parents=True, exist_ok=True)
    duration = len(samples) / audio.RATE
    destination = folder / f"clip-{datetime.now():%Y%m%d-%H%M%S}-{take_id}"
    if channels is None:
        with TemporaryDirectory(dir=folder, prefix=".capture-") as directory:
            staging = Path(directory)
            audio.write_wave(staging / "inputs.wav", samples)
            session.write_json(staging / "clip.json", {"duration_seconds": duration, "sample_rate": audio.RATE,
                               "guitar": guitar, "physical_input": input_channel,
                               "routing_unresolved": True})
            staging.rename(destination)
        diagnostics.LOG.info("Capture %s saved all four raw inputs", take_id)
        return destination, f"Clip saved · {duration:.1f}s · routing needs checking"
    diagnostics.LOG.info("Capture %s dry/wet channels %s", take_id, channels)
    dry = samples[:, [channels[0] - 1]]
    wet = samples[:, [channel - 1 for channel in channels[1:]]]
    dry_metrics, wet_metrics = audio.describe(dry), audio.describe(wet)
    if dry_metrics["lufs"] is None and wet_metrics["lufs"] is None:
        raise ValueError("No guitar signal detected. Check your Quad Cortex input.")
    with TemporaryDirectory(dir=folder, prefix=".capture-") as directory:
        staging = Path(directory)
        if context.get("usb_mapping_source") == "inferred-from-signal":
            audio.write_wave(staging / "inputs.wav", samples)
        audio.write_wave(staging / "dry.wav", dry)
        audio.write_wave(staging / "wet.wav", wet)
        session.write_json(staging / "clip.json", {**context, "duration_seconds": duration, "sample_rate": audio.RATE,
                           "guitar": guitar, "physical_input": input_channel,
                           "dry_channel": channels[0], "wet_channels": channels[1:],
                           "dry_peak_dbfs": dry_metrics["peak_dbfs"], "wet_peak_dbfs": wet_metrics["peak_dbfs"]})
        staging.rename(destination)
    if dry_metrics["clipped_samples"] or wet_metrics["clipped_samples"]:
        note = " · clipping detected"
    elif dry_metrics["lufs"] is None:
        note = " · dry input quiet"
    elif wet_metrics["lufs"] is None:
        note = " · wet output quiet"
    elif context.get("wet_preset_unverified"):
        note = " · dry + processed · preset unverified"
    else:
        note = " · dry + wet"
    diagnostics.LOG.info("Capture %s saved %s (%.2f seconds%s)", take_id, destination.name, duration, note)
    return destination, f"Clip saved · {duration:.1f}s{note}"


class ClipRecorder:
    def __init__(self, root, input_channel, leave=None):
        self.root = root
        self.input_channel = input_channel
        self.leave = leave
        self.destination = "exit"
        self.results = Queue()
        # Windows ASIO must be opened on the thread that initialized PortAudio.
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-audio")
        self.stop = None
        self.ready = Event()
        self.close_when_finished = False
        self.status = tk.StringVar(value="Ready")
        root.title("Tone Lab")
        from .widgets import BG

        root.configure(background=BG)
        root.minsize(960, 680)
        width = min(1140, max(960, root.winfo_screenwidth() - 80))
        height = min(740, max(680, root.winfo_screenheight() - 100))
        root.geometry(f"{width}x{height}")
        root.protocol("WM_DELETE_WINDOW", self.close)
        from .review import Review

        self.review = Review(root, self, latest_clip)
        if self.leave:
            top = tk.Menu(root)
            top.add_command(label="Back to listening", command=self.back_to_listening)
            root.configure(menu=top)

    def back_to_listening(self):
        self.destination = "desk"
        self.close()

    def press(self, *, guitar=None):
        if self.stop is not None:
            self.stop.set()
            self.button.configure(state="disabled")
            self.status.set("Saving clip…")
            return
        if self.review.busy():
            return

        self.stop = Event()
        self.review.stop()
        self.review.update_buttons()
        self.ready.clear()
        self.button.configure(text="Stop and save")
        self.status.set("Connecting to Quad Cortex…")
        self.worker.submit(self.record, self.stop, guitar or self.review.guitar.get())
        self.root.after(100, self.poll)

    def record(self, stop, guitar):
        try:
            _, message = record_take(self.input_channel, guitar, stop, self.ready)
        except Exception as error:
            diagnostics.LOG.exception("Clip record failed")
            message = str(error)
        self.results.put(message)

    def poll(self):
        try:
            result = self.results.get_nowait()
        except Empty:
            if self.ready.is_set() and not self.stop.is_set():
                self.status.set("Recording · dry + wet")
            self.root.after(100, self.poll)
            return
        self.stop = None
        if self.close_when_finished:
            self.close()
            return
        self.button.configure(text="Record clip", state="normal")
        if result.startswith("Clip saved"):
            self.review.pinned_take = None
        self.status.set(result)
        if not self.review.text.get().strip():
            self.review.read_state()
        else:
            self.review.update_buttons()

    def close(self):
        if self.review.live_active():
            self.review.live_panel.stop_and_close(self.close)
            return
        if self.review.pending is not None:
            self.status.set("Waiting for the Quad Cortex to finish rendering")
            self.root.after(150, self.close)
            return
        self.review.stop()
        if self.stop is None:
            self.review.shutdown()
            self.worker.shutdown(wait=False)
            if self.leave:
                self.leave(self.destination)
            else:
                self.root.destroy()
        else:
            self.close_when_finished = True
            self.press()


def launch(input_channel, *, workbench=False):
    from . import desktop

    diagnostics.configure()
    desktop.identify_process()
    screen = "workbench" if workbench else "desk"
    while screen != "exit":
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda kind, error, trace: diagnostics.LOG.error(
            "Tone Lab interface callback failed", exc_info=(kind, error, trace))
        destination = "exit"

        def leave(next_screen):
            nonlocal destination
            destination = next_screen
            root.destroy()

        if screen == "workbench":
            ClipRecorder(root, input_channel, leave)
        else:
            from .listening_room import Desk

            Desk(root, input_channel, leave)
        desktop.show_in_taskbar(root)
        diagnostics.LOG.info("Tone Lab workspace ready (%s)", screen)
        root.mainloop()
        screen = destination
