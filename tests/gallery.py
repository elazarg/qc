"""Pictures of every screen of the desk, on a simulated unit with made-up sounds, at the size the desk opens with
and at its smallest. Nothing appears on the screen (`hidden_tk`, `picture`).

    .\\.venv\\Scripts\\python.exe tests\\gallery.py [folder]

The pictures go to `folder` (by default `gallery/` at the top of the project, which git ignores), one PNG per
screen and size: `<screen>-1100.png` and `<screen>-980.png`. Run it before and after a change and lay the two
folders side by side: it is how the whole desk is looked at, not only the screen that changed.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import hidden_tk  # noqa: E402,F401  (keeps every window off the screen)
import json  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import tkinter as tk  # noqa: E402
import traceback  # noqa: E402

import numpy as np  # noqa: E402

import picture  # noqa: E402
import support  # noqa: E402
from test_lab_design import DELAY, FUZZ, GREENBACK, HIWATT, PLEXI, V30, Scripted, block  # noqa: E402

SIZES = (support.DEFAULT_SIZE, support.SMALLEST_SIZE)
PRESETS = ["Brit 2203 Lead", "Clean Twin Chorus", "Comfortably Numb Solo", "Blues Deluxe Edge", "Rectifier Rhythm",
           "AC30 Top Boost Jangle", "Acoustic Sim", "Fuzz Face Lead", "Jazz Neck Clean", "Slap Delay Country"]


def design(name, blocks, differs):
    return {"name": name, "target": "Classic rock crunch", "explanation": f"{name}, as the designer explains it.",
            "differs": differs, "scenes": [], "rows": [{"row": 0, "split_at": -1, "join_at": -1, "blocks": blocks}],
            "blend": {"level_db": 0, "spread": 0}, "sources": []}


def a_round():
    return {"aim": {"distortion": "crunch", "brightness": "balanced", "tail": "dry", "note": "A British crunch."},
            "designs": [
                design("Plexi Crunch", [block(PLEXI, 0, gain=5), block(GREENBACK, 1)],
                       "The plain route: a Plexi at half gain into Greenbacks."),
                design("Hiwatt Push", [block(FUZZ, 0), block(HIWATT, 1, gain=6), block(V30, 2)],
                       "A cleaner amp pushed by a pedal: tighter low end, more bite."),
                design("Plexi Slap", [block(PLEXI, 0, gain=7), block(V30, 1), block(DELAY, 2)],
                       "More gain and a short slap delay behind it."),
            ]}


def take(home, name, setting):
    from lab.hearing.audio import write_wave

    folder = Path(home) / "phrases" / name
    folder.mkdir(parents=True)
    time_ = np.arange(48000 * 8) / 48000
    audio = .3 * np.sin(2 * np.pi * 196 * time_) * np.exp(-2.5 * (time_ % .8)) * (.4 + .6 * np.sin(time_ * .9) ** 2)
    write_wave(folder / "dry.wav", audio[:, None].astype(np.float32))
    item = {"id": name, "instrument": "Charvel", "setting": setting, "recorded": "2026-11-20T10:00:00+00:00"}
    (folder / "phrase.json").write_text(json.dumps(item), encoding="utf-8")


class Gallery:
    def __init__(self, out):
        from lab.desk.studio import Studio
        from lab.desk.window import Desk
        from lab.qc.host import Host
        from lab.sound.assistant import Assistant

        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.home = Path(tempfile.mkdtemp(prefix="tone-lab-gallery-"))
        take(self.home, "take-1", "Bridge · Series")
        take(self.home, "take-2", "Neck · Parallel")
        rows = [{"slot": {"folder_key": "/media/p4/Presets/My Presets", "position": index * 3, "is_factory": False},
                 "name": name, "setlist": "My Presets"} for index, name in enumerate(PRESETS)]
        (self.home / "directory.json").write_text(json.dumps({"presets": rows}), encoding="utf-8")
        host = Host(self.home / "unit", simulated=True, local=True)
        host.ready.result(20)
        self.script = Scripted()
        self.studio = Studio(host, self.home, assistant=Assistant(self.home, automatic=False, run=self.script))
        self.studio.idle_seconds = 0.
        self.root = tk.Tk()
        self.desk = Desk(self.root, self.studio)
        self.taken = []
        self.pump()

    def pump(self, seconds=60):
        try:
            support.settle(self.studio, seconds, self.root)
        except AssertionError as error:
            print("  (still working:", error, ")")

    def shoot(self, name, action=lambda: None, *, settle=True):
        try:
            action()
            if settle:
                self.pump()
            for width, height in SIZES:
                self.root.geometry(f"{width}x{height}")
                self.root.update()
                self.root.update()
                picture.save(self.root, str(self.out / f"{name}-{width}.png"))
            self.root.geometry("{}x{}".format(*support.DEFAULT_SIZE))
            self.root.update()
            self.taken.append(name)
        except Exception:  # noqa: BLE001  one screen that fails does not keep the others from being pictured
            print("FAILED", name)
            traceback.print_exc()

    def panel(self, name):
        if self.desk.panel != name:
            self.desk.toggle(name)

    def run(self):
        desk, studio = self.desk, self.studio
        self.shoot("desk-empty")
        self.shoot("instrument-list", desk.chooser.open, settle=False)
        desk.chooser.close()
        # A request in flight, held until it has been pictured.
        gate = threading.Event()
        answered = a_round()
        studio.assistant.run = lambda *args, **options: (gate.wait(30), answered)[1]
        studio.ask("Comfortably Numb, the second solo")
        self.shoot("request-in-flight", settle=False)
        gate.set()
        self.pump()
        studio.assistant.run = self.script
        self.shoot("round")
        def search():
            desk.search.open()
            desk.search.query.set("Comfortably")

        self.shoot("search", search)
        desk.search.close()
        self.shoot("copied-to-a", studio.copy_to_a)
        self.shoot("second-design", lambda: studio.step(1))
        self.shoot("guitar-on-b", lambda: desk.studio.play("guitar"))
        self.shoot("phrase-on-b", lambda: desk.toggle_play("phrase"))
        studio.stop()
        self.shoot("saving", desk.save)
        desk.save_row.pack_forget()
        self.shoot("blind", studio.blind)
        studio.blind()
        self.shoot("remark", lambda: studio.ask("a bit tighter"))

        def recording():
            # Half way through a take of eight seconds. (The simulated unit records in no time, so the state is
            # set as the studio sets it while a real take is recorded.)
            from time import monotonic

            studio.doing["record"] = "Recording · play"
            studio.recording = (monotonic() - 12., 45.)
            studio._set()

        self.shoot("recording", recording, settle=False)
        del studio.doing["record"]
        studio.recording = None
        studio._set()

        def scenes():
            from lab.sound.chain import Chain
            from lab.sound.sounds import Sound

            studio.copy_to_a()
            named = Chain(rows=studio.sound("b").chain.rows, labels=("Rhythm", "Lead", "Solo"))
            studio.put("b", Sound("Three scenes", named, {"kind": "designed"}))

        self.shoot("scenes", scenes)
        studio.undo()
        from lab.desk.window import PANELS

        for name in PANELS:
            self.shoot(f"panel-{name.lower()}", lambda name=name: self.panel(name))
        self.shoot("panel-difference-measured", lambda: (self.panel("Difference"), desk.measure_difference()))
        self.panel("Sounds")
        for kind in ("IRs", "Captures"):
            def show(kind=kind):
                desk.sounds_kind = kind
                studio.read_assets(lambda _: desk.refill("Sounds"))

            self.shoot(f"panel-sounds-{kind.lower()}", show)
        desk.sounds_kind = "Presets"
        from lab.tools.menu import TOOLS, opener

        self.panel("More")
        for title, name in TOOLS:
            self.shoot(f"tool-{name}", lambda name=name: opener(name)(desk))
        desk.back()
        from lab.learner import screen as learning

        for view in (learning.LEARNER, learning.TEACH, learning.IDLE):
            self.shoot(f"learning-{view.split()[0].lower()}", lambda view=view: self.learn(view))
        desk.leave_learning()
        self.pump()
        desk.close()

    def learn(self, view):
        shown = self.desk.learn(view)
        for _ in range(200):
            self.studio.poll()
            self.desk.seat.tick()
            self.root.update()
            learner = shown.learner
            if learner is None or (learner.job is None and not learner.queued):
                break


def main(arguments):
    out = Path(arguments[0]) if arguments else HERE.parent / "gallery"
    gallery = Gallery(out)
    gallery.run()
    print(f"{len(gallery.taken)} screens, {len(gallery.taken) * len(SIZES)} pictures in {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
