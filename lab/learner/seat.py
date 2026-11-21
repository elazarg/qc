"""The Guitar Learner's seat at the desk: what the learner, teaching a control and gestures ask of the desk
that opens them.

    seat = LearnerSeat(desk)           # the desk keeps it as desk.seat and calls seat.tick() on its clock

What the seat gives:

    seat.folder                         where the learner's data is kept (inside the desk's data folder)
    seat.guitar, seat.input_channel     the instrument chosen on the desk, and the input it is plugged into
    seat.host                           the engine's host: `host.call("inputs", n)` says where the dry guitar is
    seat.submit(message, work, *args, done=..., stop=...)
                                        the one recording or listening at a time, on the seat's thread
    seat.later(future, done)            `done(result)` on the desk's clock when a future finishes
    seat.watch(step)                    `step()` on every tick of the desk's clock, until it returns False
    seat.set_status(text)               the desk's status line

The audio driver has one owner at a time. Before a recording or a listening
opens it, `submit` takes the desk's audio hold (`studio.hold_audio`), which
stops what the desk plays and has the engine close its own stream; the work
starts only when the engine has done so, and the hold is given back when the
work ends, however it ends.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as Late
import os
from pathlib import Path
import sys

from lab.learner import folders
from lab.learner.folders import LOG

QUIET_TIMEOUT = 20.0        # how long the engine may take to let go of the audio driver


def is_the_players_home(folder):
    """Whether `folder` is the app's real data folder in the player's profile, where the earlier app's learner
    data belongs. Never for a folder chosen with TONE_LAB_HOME, a simulated desk's folder, or a test's."""
    if os.environ.get("TONE_LAB_HOME"):
        return False
    base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    return Path(folder).resolve() == (Path(base) / "Tone Lab").resolve()


class LearnerSeat:
    HOLD = "learner"

    def __init__(self, desk, *, real_audio=True):
        """`real_audio` is False for a desk whose unit is simulated (`host.simulated`): the learner's recordings
        and listening open the Quad Cortex's real audio driver, which has nothing to do with a simulated unit, so
        `submit` refuses them."""
        self.desk, self.root, self.studio = desk, desk.root, desk.studio
        self.real_audio = real_audio
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="learner")
        self.job = self.done = None         # the recording or listening under way; the desk reads `job`
        self.stop_signal = None             # ends it; the desk sets it when closing
        self.waiting, self.watchers, self.faulted = [], [], set()
        self._leaving = False
        home = Path(self.studio.folder)
        self.folder = home / "learner"
        folders.use(self.folder)
        folders.log_to(home / "errors.log")
        if is_the_players_home(home):
            try:
                if folders.bring_old(new=self.folder):
                    self.set_status(f"The Guitar Learner's recordings and labels were copied to {self.folder}.")
            except OSError:
                LOG.exception("The earlier Guitar Learner data could not be copied to %s", self.folder)
        desk.seat = self

    # ---- what the desk has chosen ----

    @property
    def guitar(self):
        return self.studio.state.instrument

    @property
    def input_channel(self):
        return (self.studio.store.instrument(self.guitar) or {}).get("input", 1)

    @property
    def host(self):
        return self.studio.host

    def set_status(self, value, **_):
        self.studio.set(status=value)

    # ---- closing ----

    @property
    def leaving(self):
        return self._leaving

    @leaving.setter
    def leaving(self, value):
        self._leaving = bool(value)
        if not value:
            return
        if self.stop_signal is not None:
            self.stop_signal.set()
        gestures = sys.modules.get("lab.learner.gestures")      # nothing to stop if it was never used
        if gestures is not None:
            gestures.stop()
        self.studio.release_audio(self.HOLD)

    # ---- the one recording or listening ----

    def submit(self, message, work, *args, done, stop=None):
        """Run `work(*args)` on the seat's thread with the audio driver held for the learner, then call
        `done((result, None))` or `done((None, why it failed))` on the desk's clock. False when it cannot start."""
        if self.job is not None or self._leaving:
            return False
        if not self.real_audio:
            self.set_status("Not with a simulated unit.")
            return False
        holder = self.studio.audio_held_by
        if holder not in (None, self.HOLD):
            self.set_status(f"The audio input is in use ({holder}). Stop that first.")
            return False
        let_go = self.studio.hold_audio(self.HOLD)
        self.done, self.stop_signal = done, stop
        self.set_status(message)

        def run():
            try:
                wait_for(let_go)
                return work(*args), None
            except ValueError as error:         # something the player can put right: said on the line
                return None, str(error)
            except Exception as error:  # noqa: BLE001 - said on the line, and written with its trace
                LOG.exception("The Guitar Learner's %s failed", getattr(work, "__name__", "work"))
                return None, str(error) or type(error).__name__

        self.job = self.worker.submit(run)
        return True

    def later(self, future, done, failed=None):
        """Call `done(result)` on the desk's clock when `future` finishes; `failed(error)`, or the status line,
        when it fails."""
        self.waiting.append((future, done, failed))

    def watch(self, step):
        """Call `step()` on every tick of the desk's clock until it returns False. A step that raises is
        reported once and called again on the next tick: one fault does not stop what it was following."""
        if step not in self.watchers:
            self.watchers.append(step)

    def tick(self):
        if self.job is not None and self.job.done():
            job, done = self.job, self.done
            self.job = self.done = self.stop_signal = None
            self.studio.release_audio(self.HOLD)
            self._call(done, job.result())
        for item in [item for item in self.waiting if item[0].done()]:
            self.waiting.remove(item)
            future, done, failed = item
            try:
                result = future.result()
            except Exception as error:  # noqa: BLE001
                LOG.warning("A Guitar Learner action did not finish: %s", error, exc_info=True)
                self._call(failed or (lambda error: self.set_status(f"Guitar Learner: {error}")), error)
                continue
            self._call(done, result)
        for step in list(self.watchers):
            try:
                keep = step()
            except Exception as error:  # noqa: BLE001
                if step not in self.faulted:
                    self.faulted.add(step)
                    LOG.exception("A Guitar Learner step failed")
                    self.set_status(f"Guitar Learner: {error}")
                continue
            if keep is False and step in self.watchers:
                self.watchers.remove(step)
                self.faulted.discard(step)

    def _call(self, handler, value):
        try:
            handler(value)
        except Exception as error:  # noqa: BLE001
            LOG.exception("A Guitar Learner result could not be handled")
            self.set_status(f"Guitar Learner: {error}")


def wait_for(let_go, timeout=QUIET_TIMEOUT):
    """Wait until the engine has closed its own audio stream (the future `studio.hold_audio` returns).

    An engine that is not there cannot be holding the driver, so its failure to answer is not a reason to stop;
    one that is busy past the wait still may be, and is."""
    if let_go is None:
        return
    try:
        let_go.result(timeout)
    except Late as error:
        raise RuntimeError("The desk did not let go of the audio input in time. Try again in a moment.") from error
    except Exception as error:  # noqa: BLE001
        LOG.warning("The engine did not confirm it is quiet (%s); it holds no stream", error)
