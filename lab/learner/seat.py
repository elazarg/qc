"""The Guitar Learner's seat at the desk: what the learner, input calibration and gestures ask of the desk
that opens them."""

import json
from pathlib import Path


class LearnerSeat:
    """What the Guitar Learner asks of the desk that opens it: a worker for its recording and live listening,
    the status line, and whether something else is using the audio input.

    The learner records the guitar's dry input itself, so the seat stops the phrase or the guitar the desk is
    playing before it starts, and the desk does not start playing while it works.
    """

    def __init__(self, desk):
        from concurrent.futures import ThreadPoolExecutor

        self.desk, self.root = desk, desk.root
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="learner")
        self.job = self.task = self.done = None
        self.leaving = None
        self.ready = self.stop_signal = None
        self.live_is_ready = False
        self.takes = []
        instrument = desk.studio.store.instrument(desk.studio.state.instrument) or {}
        self.input_channel = instrument.get("input", 1)
        desk.seat = self

    @staticmethod
    def read_state(path):
        try:
            found = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def set_status(self, value, **_):
        self.desk.studio.set(status=value)

    def task_if_active(self, name):
        return self.job is not None and self.task == name

    def stop(self):
        if self.stop_signal is not None:
            self.stop_signal.set()

    def submit(self, task, message, work, *args, done, **options):
        if self.job is not None or self.leaving:
            return
        self.desk.studio.stop()
        self.task, self.done = task, done
        self.set_status(message)
        self.job = self.worker.submit(work, *args, **options)

    def tick(self):
        if self.job is None or not self.job.done():
            return
        job, done = self.job, self.done
        self.job = self.task = self.done = None
        try:
            done(job.result())
        except Exception as error:  # noqa: BLE001
            self.set_status(f"Guitar Learner: {error}")
