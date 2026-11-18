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

from . import audio, device, diagnostics, session


def latest_clip(*, pair=False):
    folder = session.SESSIONS / "recordings"
    clips = [*folder.glob("clip-*.wav"), *folder.glob("clip-*/dry.wav"),
             *folder.glob("clip-*/inputs.wav")]
    newest = max(clips, key=lambda clip: clip.stat().st_mtime_ns, default=None)
    if newest is None:
        raise ValueError("There is no clip yet. Open Start Tone Lab and record a riff.")
    if newest.name == "inputs.wav":
        if not pair:
            raise RuntimeError("The last take was saved as raw USB inputs. Its dry and wet mapping needs to be verified.")
        info = json.loads((newest.parent / "clip.json").read_text(encoding="utf-8"))
        return {**info, "raw": str(newest), "dry": None, "wet": None}
    if pair:
        if newest.name != "dry.wav":
            return {"dry": str(newest), "wet": None}
        info = json.loads((newest.parent / "clip.json").read_text(encoding="utf-8"))
        return {**info, "dry": str(newest), "wet": str(newest.with_name("wet.wav"))}
    return newest


class ClipRecorder:
    def __init__(self, root, input_channel):
        self.root = root
        self.input_channel = input_channel
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

    def press(self):
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
        self.worker.submit(self.record, self.stop)
        self.root.after(100, self.poll)

    def record(self, stop):
        take_id = uuid4().hex[:6]
        try:
            diagnostics.LOG.info("Capture %s requested on physical input %d", take_id, self.input_channel)
            # Capture both USB pairs immediately. Resolve their meaning while the take runs.
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-routing") as routing:
                channel_mapping = routing.submit(device.recording_channels, self.input_channel, include_context=True)
                diagnostics.LOG.info("Capture %s opening ASIO inputs 1–4 at %d Hz", take_id, audio.RATE)
                samples = audio.record_inputs(audio.MAX_SECONDS, (1, 2, 3, 4), stop=stop, ready=self.ready)
                try:
                    channels, context = channel_mapping.result()
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
                                       "physical_input": self.input_channel, "routing_unresolved": True})
                    staging.rename(destination)
                diagnostics.LOG.info("Capture %s saved all four raw inputs", take_id)
                self.results.put(f"Clip saved · {duration:.1f}s · routing needs checking")
                return
            diagnostics.LOG.info("Capture %s dry/wet channels %s", take_id, channels)
            dry = samples[:, [channels[0] - 1]]
            wet = samples[:, [channel - 1 for channel in channels[1:]]]
            dry_metrics, wet_metrics = audio.describe(dry), audio.describe(wet)
            if dry_metrics["lufs"] is None and wet_metrics["lufs"] is None:
                raise ValueError("No guitar signal detected. Check your Quad Cortex input.")
            with TemporaryDirectory(dir=folder, prefix=".capture-") as directory:
                staging = Path(directory)
                audio.write_wave(staging / "dry.wav", dry)
                audio.write_wave(staging / "wet.wav", wet)
                session.write_json(staging / "clip.json", {**context, "duration_seconds": duration, "sample_rate": audio.RATE,
                                   "physical_input": self.input_channel,
                                   "dry_channel": channels[0], "wet_channels": channels[1:],
                                   "dry_peak_dbfs": dry_metrics["peak_dbfs"], "wet_peak_dbfs": wet_metrics["peak_dbfs"]})
                staging.rename(destination)
            if dry_metrics["clipped_samples"] or wet_metrics["clipped_samples"]:
                note = " · clipping detected"
            elif dry_metrics["lufs"] is None:
                note = " · dry input quiet"
            elif wet_metrics["lufs"] is None:
                note = " · wet output quiet"
            else:
                note = " · dry + wet"
            diagnostics.LOG.info("Capture %s saved %s (%.2f seconds%s)", take_id, destination.name, duration, note)
            self.results.put(f"Clip saved · {duration:.1f}s{note}")
        except Exception as error:
            diagnostics.LOG.exception("Capture %s failed", take_id)
            self.results.put(str(error))

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
            return
        self.review.stop()
        if self.stop is None:
            self.review.shutdown()
            self.worker.shutdown(wait=False)
            self.root.destroy()
        else:
            self.close_when_finished = True
            self.press()


def launch(input_channel):
    from . import desktop

    diagnostics.configure()
    diagnostics.LOG.info("Clip recorder started")
    desktop.identify_process()
    root = tk.Tk()
    ClipRecorder(root, input_channel)
    desktop.show_in_taskbar(root)
    root.mainloop()
