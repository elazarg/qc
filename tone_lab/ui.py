"""Desktop front end for recording, reamping, and blind auditions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4
import winsound

from . import audio, device, diagnostics, session

BACKGROUND = "#151a1d"
SURFACE = "#20272a"
TEXT = "#f0f1e8"
SECONDARY = "#b8c3bc"
ACCENT = "#d3ec9c"
WAV = [("WAV audio", "*.wav")]


class ToneLab:
    def __init__(self, root):
        self.root = root
        self.root.title("Tone Lab · Quad Cortex")
        self.root.configure(background=BACKGROUND)
        self.root.minsize(740, 590)
        self.guitar = tk.StringVar(value="Charvel")
        self.pickup = tk.StringVar()
        self.target = tk.StringVar()
        self.di = tk.StringVar()
        self.reference = tk.StringVar()
        self.seconds = tk.StringVar(value="10")
        self.channel = tk.StringVar(value="1")
        self.scenes = tk.StringVar(value="AB")
        self.notes = tk.StringVar()
        self.status = tk.StringVar(value="Ready. Use Offline demo to try the workflow in silence.")
        self.result_text = tk.StringVar(value="Run a comparison or open an existing session.")
        self.folder = None
        self.auditions = {}
        self.busy = False
        self.buttons = []
        self.results = Queue()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workbench-audio")
        self.style()
        self.layout()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BACKGROUND, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Panel.TFrame", background=SURFACE)
        style.configure("Panel.TLabel", background=SURFACE, foreground=TEXT)
        style.configure("Hint.TLabel", background=SURFACE, foreground=SECONDARY)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 24), foreground=ACCENT)
        style.configure("Section.TLabel", background=SURFACE, font=("Segoe UI Semibold", 13))
        style.configure("TButton", background="#344043", foreground=TEXT, padding=(12, 6), borderwidth=0)
        style.map("TButton", background=[("active", "#46565a")], foreground=[("disabled", "#9ba4a0")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#1d2914")
        style.map("Accent.TButton", background=[("active", "#e1f5b6"), ("disabled", "#344043")])
        style.configure("TEntry", fieldbackground="#131819", foreground=TEXT, insertcolor=TEXT, padding=5)
        style.configure("TCombobox", fieldbackground="#131819", foreground=TEXT, padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", "#131819")], foreground=[("readonly", TEXT)])

    def button(self, parent, label, command, *, accent=False):
        button = ttk.Button(parent, text=label, command=command,
                            style="Accent.TButton" if accent else "TButton")
        self.buttons.append(button)
        return button

    def layout(self):
        shell = ttk.Frame(self.root, padding=(24, 18))
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="Tone Lab", style="Title.TLabel").pack(side="left")
        self.button(header, "Offline demo", self.demo).pack(side="right", padx=(8, 0))
        self.button(header, "Check Quad Cortex", self.check).pack(side="right")

        setup = ttk.Frame(shell, style="Panel.TFrame", padding=15)
        setup.pack(fill="x", pady=(0, 12))
        setup.columnconfigure(1, weight=1)
        setup.columnconfigure(3, weight=1)
        ttk.Label(setup, text="Guitar and passage", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        self.label(setup, "Guitar", 1, 0)
        ttk.Combobox(setup, values=session.GUITARS, state="readonly", textvariable=self.guitar, width=15).grid(row=1, column=1, sticky="w")
        self.label(setup, "Pickup", 1, 2)
        ttk.Entry(setup, textvariable=self.pickup).grid(row=1, column=3, sticky="ew")
        self.label(setup, "Song / section", 2, 0)
        ttk.Entry(setup, textvariable=self.target).grid(row=2, column=1, columnspan=3, sticky="ew", pady=(7, 0))
        self.file_row(setup, "Dry DI WAV", self.di, 3)
        self.file_row(setup, "Reference WAV", self.reference, 4)

        actions = ttk.Frame(shell, style="Panel.TFrame", padding=15)
        actions.pack(fill="x", pady=(0, 12))
        ttk.Label(actions, text="Record and compare", style="Section.TLabel").grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 10))
        self.label(actions, "DI channel", 1, 0)
        ttk.Combobox(actions, values=(1, 2, 3, 4), state="readonly", width=3, textvariable=self.channel).grid(row=1, column=1)
        self.label(actions, "Seconds", 1, 2)
        ttk.Entry(actions, width=5, textvariable=self.seconds).grid(row=1, column=3)
        self.button(actions, "Record DI", self.record).grid(row=1, column=4, padx=8, sticky="w")
        self.label(actions, "Scenes", 2, 0)
        ttk.Entry(actions, width=5, textvariable=self.scenes).grid(row=2, column=1, pady=(6, 0))
        self.button(actions, "Reamp scenes", self.reamp, accent=True).grid(row=2, column=2, columnspan=2, padx=8, pady=(6, 0), sticky="w")
        self.button(actions, "Compare WAVs", self.imported).grid(row=2, column=4, padx=8, pady=(6, 0), sticky="w")

        audition = ttk.Frame(shell, style="Panel.TFrame", padding=15)
        audition.pack(fill="x", pady=(0, 10))
        audition.columnconfigure(5, weight=1)
        ttk.Label(audition, text="Blind audition", style="Section.TLabel").grid(row=0, column=0, columnspan=5, sticky="w")
        self.button(audition, "Open session", self.open_session).grid(row=0, column=5, sticky="e")
        ttk.Label(audition, textvariable=self.result_text, style="Hint.TLabel", wraplength=650).grid(row=1, column=0, columnspan=6, sticky="w", pady=6)
        self.play_buttons = {}
        for col, label in enumerate("ABC"):
            button = self.button(audition, f"Play {label}", lambda key=label: self.play(key))
            self.play_buttons[label] = button
            button.grid(row=2, column=col, padx=(0, 6))
        self.button(audition, "Stop", self.stop).grid(row=2, column=3)
        self.button(audition, "Open files", self.open_files).grid(row=2, column=5, sticky="e")
        self.label(audition, "Preference", 3, 0)
        ttk.Entry(audition, textvariable=self.notes).grid(row=3, column=1, columnspan=4, sticky="ew", padx=(0, 6), pady=(7, 0))
        self.button(audition, "Keep last played", self.keep).grid(row=3, column=5, sticky="e", pady=(7, 0))
        ttk.Label(shell, textvariable=self.status, wraplength=680).pack(fill="x", anchor="w")
        self.update_buttons()

    def label(self, parent, label, row, column):
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=column, sticky="w", padx=(0, 8), pady=3)

    def file_row(self, panel, label, variable, row):
        self.label(panel, label, row, 0)
        ttk.Entry(panel, textvariable=variable).grid(row=row, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=(6, 0))
        self.button(panel, "Browse…", lambda: self.select_file(variable)).grid(row=row, column=3, sticky="e", pady=(6, 0))

    def select_file(self, variable):
        path = filedialog.askopenfilename(filetypes=WAV, parent=self.root)
        if path:
            variable.set(path)

    def update_buttons(self):
        for button in self.buttons:
            button.state(["disabled"] if self.busy else ["!disabled"])
        for label, button in self.play_buttons.items():
            if label not in self.auditions:
                button.state(["disabled"])

    def start(self, message, work, done=None):
        self.stop()
        self.busy = True
        self.status.set(message)
        self.update_buttons()

        def worker():
            try:
                self.results.put(("done", work()))
            except Exception as error:
                diagnostics.LOG.exception("Workbench operation failed: %s", message)
                self.results.put(("error", str(error)))

        def poll():
            try:
                kind, value = self.results.get_nowait()
            except Empty:
                self.root.after(100, poll)
                return
            if kind == "progress":
                self.status.set(value)
                self.root.after(100, poll)
                return
            self.busy = False
            self.update_buttons()
            if kind == "error":
                self.status.set(value)
                messagebox.showerror("Tone Lab", value, parent=self.root)
            elif done:
                done(value)

        self.worker.submit(worker)
        self.root.after(100, poll)

    def check(self):
        def work():
            with device.connect() as qc:
                info = device.inspect(qc)
            audio.check_asio()
            return info

        def done(info):
            scene = f" · scene {info['scene']}" if info["scene"] else ""
            self.status.set(f"CorOS {info['firmware']} · {info['preset']}{scene} · ASIO ready at 48 kHz")

        self.start("Checking the device without starting audio…", work, done)

    def demo(self):
        guitar = self.guitar.get()
        self.start("Generating a silent demo…", lambda: session.demo(guitar), self.load)

    def record(self):
        try:
            seconds, channel = float(self.seconds.get()), int(self.channel.get())
            if not 0.5 <= seconds <= audio.MAX_SECONDS:
                raise ValueError
        except ValueError:
            self.status.set("Enter a duration from 0.5 to 45 seconds.")
            return
        guitar = self.guitar.get()
        path = session.SESSIONS / "recordings" / f"{datetime.now():%Y%m%d-%H%M%S}-{guitar.replace(' ', '-').lower()}-{uuid4().hex[:6]}.wav"

        def work():
            samples = audio.record_di(seconds, channel)
            path.parent.mkdir(parents=True, exist_ok=True)
            audio.write_wave(path, samples)
            return audio.describe(samples)

        def done(metrics):
            self.di.set(str(path))
            self.status.set(f"DI saved · peak {metrics['peak_dbfs']:.1f} dBFS · {metrics['clipped_samples']} clipped samples")

        self.start(f"Recording {guitar} DI now…", work, done)

    def inputs(self):
        return self.guitar.get(), self.pickup.get().strip(), self.target.get().strip()

    def reamp(self):
        if not self.di.get().strip():
            self.status.set("Choose or record a dry DI WAV first.")
            return
        if not messagebox.askokcancel("Start Quad Cortex reamp?",
                                     "Load your Tone Lab test preset and close Cortex Control. "
                                     "This sends your DI to Quad Cortex USB 5/6.", parent=self.root):
            return
        inputs, dry, scenes = self.inputs(), self.di.get(), self.scenes.get()
        reference = self.reference.get().strip() or None
        self.start("Checking the test preset and USB routing…",
                   lambda: session.compare_qc(*inputs, dry, scenes, reference, arm=True,
                                              progress=lambda item: self.results.put(("progress", item))), self.load)

    def imported(self):
        paths = filedialog.askopenfilenames(title="Choose two or more wet WAVs", filetypes=WAV, parent=self.root)
        if paths:
            inputs, reference = self.inputs(), self.reference.get().strip() or None
            self.start("Measuring the WAVs…", lambda: session.imported(*inputs, paths, reference), self.load)

    def load(self, folder):
        manifest = session.load_session(folder)
        self.folder = Path(folder)
        self.auditions = manifest.get("auditions", {})
        self.last_played = None
        self.notes.set("")
        kind = "Offline demo" if manifest["kind"] == "demo" else manifest["guitar"]
        self.result_text.set(f"{kind} · {len(self.auditions)} level-matched clips · {manifest['target'] or 'Untitled passage'}")
        self.status.set("Comparison ready. Audio starts when you choose Play.")
        self.update_buttons()

    def open_session(self):
        path = filedialog.askdirectory(initialdir=session.SESSIONS if session.SESSIONS.exists() else Path.cwd(), parent=self.root)
        if path:
            try:
                self.load(path)
            except (OSError, ValueError, KeyError) as error:
                messagebox.showerror("Open session", str(error), parent=self.root)

    def play(self, label):
        path = self.folder / self.auditions[label]["file"]
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except RuntimeError as error:
            self.status.set(str(error))
            return
        self.last_played = label
        self.status.set(f"Playing {label}")

    @staticmethod
    def stop():
        winsound.PlaySound(None, 0)

    def keep(self):
        if not self.folder or not self.last_played:
            self.status.set("Play a clip before saving your preference.")
            return
        try:
            session.choose(self.folder, self.last_played, self.notes.get().strip())
        except (OSError, ValueError) as error:
            self.status.set(str(error))
            return
        self.status.set(f"Saved your preference for {self.last_played} in the session.")

    def open_files(self):
        if self.folder:
            os.startfile(self.folder)

    def close(self):
        if not self.busy:
            self.stop()
            self.worker.shutdown(wait=False)
            self.root.destroy()
        else:
            self.status.set("Wait for this operation to finish before closing Tone Lab.")


def launch():
    diagnostics.configure()
    diagnostics.LOG.info("Workbench started")
    root = tk.Tk()
    ToneLab(root)
    root.mainloop()
