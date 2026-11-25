"""The engine in a process of its own, holding the unit for the whole session.

The desk sends one command at a time and gets a `Future` back. Commands run
in order. `stop()` ends the command running at its next safe point (a long
command checks a flag between two writes) and drops the commands sent before
it that have not begun. A command sent after `stop()`, such as the one that
disconnects the inputs, always runs.

A command may report progress while it runs (`progress=` on `call`), which
recording uses to say when the input is actually open.

Running the engine in its own process means a failure of the audio driver
cannot take the desk down. When that process ends unexpectedly, the next one
gives back the player's preset when it opens, from the record the engine
left on disk.

`Host(local=...)` runs the same engine on a thread instead, for tests and
for the simulated unit.

A command that fails raises `Fault` from its future: the message, the kind of
failure (the name of what was raised where the unit is held, such as
"Stopped", "Moved" or "Refused") and the trace from there. The trace is kept
because the worker has nowhere of its own to write it: started without a
console, what it prints is lost.
"""

from concurrent.futures import Future
import multiprocessing
from queue import Empty
import threading
import traceback


# Outcomes that are part of ordinary use, not faults: said on the line, never logged with a trace.
EXPECTED = frozenset({"Stopped", "Played", "Moved", "Unsaved", "Refused", "NotOurs", "EditorActive"})


class Fault(RuntimeError):
    """A command failed where the unit is held. `kind` names what was raised there and `trace` is its trace
    there ("" when there is none, as for a command dropped before it began)."""

    def __init__(self, message, kind="RuntimeError", trace=""):
        super().__init__(message)
        self.kind, self.trace = kind, trace

    @property
    def expected(self):
        return self.kind in EXPECTED


def _traced(device):
    """The device as it is, or, when TONE_LAB_TRACE names a file, with every call on it written there
    (`lab.qc.trace`). A trace holds the player's presets: keep it in the data folder or in `personal/`."""
    import os

    path = os.environ.get("TONE_LAB_TRACE")
    if not path:
        return device
    from lab.qc.trace import recording

    return recording(device, path)


def _engine(simulated, folder, physical_input):
    from lab.qc.engine import Engine
    from lab.qc.slots import Slots
    from lab.qc.unit import Unit

    if simulated:
        from lab.qc.sim import SimAudio, SimUnit

        device = SimUnit()
        unit = Unit(_traced(device), settle=.05, sleep=lambda seconds: None)
        audio = SimAudio(device)
        context = None
    else:
        from lab.qc import device as qc_device
        from lab.hearing.audio import Audio

        context = qc_device.connect(write=True)
        unit = Unit(_traced(context.__enter__()))
        audio = Audio()
    return Engine(unit, Slots(unit, folder), audio, physical_input=physical_input), context


def _serve(commands, replies, stop, stopped_before, simulated, folder, physical_input):
    from lab.hearing.audio import Cancelled, Played

    try:
        engine, context = _engine(simulated, folder, physical_input)
    except Exception as error:  # noqa: BLE001
        replies.put((None, False, (type(error).__name__, f"{type(error).__name__}: {error}",
                                   traceback.format_exc())))
        return
    replies.put((None, True, "ready"))
    try:
        while True:
            ticket, name, args, options = commands.get()
            if name == "quit":
                break
            # A stop covers the commands sent before it. Clear the flag first, then look: a stop that comes in
            # between is then either seen here or leaves the flag set for this command.
            stop.clear()
            if ticket <= stopped_before.value:
                replies.put((ticket, False, ("Stopped", "Stopped before it began.", "")))
                continue
            if options.pop("_progress", False):
                options = {**options, "progress": lambda value, ticket=ticket: replies.put((ticket, "progress", value))}
            try:
                if name in ("show", "hear", "hear_shaped", "record"):
                    options = {**options, "stop": stop}
                if "." in name:
                    # A side tool's own operation, `lab.tools.x.function(engine, ...)`, run where the unit is held.
                    from importlib import import_module

                    module, function = name.rsplit(".", 1)
                    if not module.startswith("lab."):
                        raise ValueError(f"Not an operation of this app: {name}")
                    result = getattr(import_module(module), function)(engine, *args, **{**options, "stop": stop})
                else:
                    result = getattr(engine, name)(*args, **options)
                replies.put((ticket, True, result))
            except Exception as error:  # noqa: BLE001
                # A replay the audio driver ended because of a stop is a stop like one between two writes; one the
                # guitar's first note ended is "Played".
                kind = ("Played" if isinstance(error, Played) else "Stopped" if isinstance(error, Cancelled)
                        else type(error).__name__)
                replies.put((ticket, False, (kind, str(error) or kind, traceback.format_exc())))
    finally:
        try:
            engine.close()
        finally:
            if context is not None:
                context.__exit__(None, None, None)


class _Number:
    """A shared integer for the worker on a thread, as `multiprocessing.Value` is for one in a process."""

    def __init__(self):
        self.value = 0


class Host:
    def __init__(self, folder, *, simulated=False, local=False, physical_input=1):
        self.futures, self.ticket, self.lock = {}, 0, threading.Lock()
        self.progress = {}                  # ticket -> what to call with a command's progress
        self.failed = None                  # why the worker could not start or stopped, once it has
        self.simulated = simulated          # a simulated unit: nothing here opens real hardware
        if local:
            from queue import Queue

            self.commands, self.replies, self.stop_flag = Queue(), Queue(), threading.Event()
            self.stopped_before = _Number()
            self.worker = threading.Thread(target=_serve, daemon=True,
                                           args=(self.commands, self.replies, self.stop_flag, self.stopped_before,
                                                 simulated, str(folder), physical_input))
        else:
            context = multiprocessing.get_context("spawn")
            self.commands, self.replies, self.stop_flag = context.Queue(), context.Queue(), context.Event()
            self.stopped_before = context.Value("q", 0, lock=False)
            self.worker = context.Process(target=_serve, daemon=True,
                                          args=(self.commands, self.replies, self.stop_flag, self.stopped_before,
                                                simulated, str(folder), physical_input))
        self.worker.start()
        self.ready = Future()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        while True:
            try:
                ticket, ok, value = self.replies.get(timeout=.5)
            except Empty:
                if not self.worker.is_alive():
                    self._fail_all("The part of the app that holds the Quad Cortex stopped.")
                    return
                continue
            except (EOFError, OSError):
                self._fail_all("The part of the app that holds the Quad Cortex stopped.")
                return
            if ticket is None:
                if ok:
                    self.ready.set_result(value)
                    continue
                # The worker could not start: every command waiting, and every one sent from now on, fails.
                kind, message, trace = value
                self._fail_all(f"The Quad Cortex could not be opened: {message}", kind, trace)
                return
            if ok == "progress":
                listener = self.progress.get(ticket)
                if listener is not None:
                    listener(value)
                continue
            with self.lock:
                future = self.futures.pop(ticket, None)
                self.progress.pop(ticket, None)
            if future is not None:
                if ok:
                    future.set_result(value)
                else:
                    kind, message, trace = value
                    future.set_exception(Fault(message, kind, trace))

    def _fail_all(self, message, kind="WorkerGone", trace=""):
        with self.lock:
            self.failed = (message, kind, trace)
            pending, self.futures = list(self.futures.values()), {}
        for future in pending:
            future.set_exception(Fault(message, kind, trace))
        if not self.ready.done():
            self.ready.set_exception(Fault(message, kind, trace))

    def call(self, name, *args, progress=None, **options):
        """Send a command; its `Future` holds the result. `progress`, if given, is called (on another thread) with
        whatever the command reports while it runs."""
        future = Future()
        with self.lock:
            if self.failed is not None:
                future.set_exception(Fault(*self.failed))
                return future
            self.ticket += 1
            self.futures[self.ticket] = future
            ticket = self.ticket
            if progress is not None:
                self.progress[ticket] = progress
                options = {**options, "_progress": True}
        self.commands.put((ticket, name, args, options))
        return future

    def stop(self):
        """End the running command at its next safe point, and drop the ones sent before now that have not begun.
        Commands sent after this run as usual."""
        with self.lock:
            self.stopped_before.value = self.ticket
            if self.futures:
                self.stop_flag.set()

    @property
    def busy(self):
        with self.lock:
            return bool(self.futures)

    def close(self, timeout=15):
        self.stop()
        self.commands.put((0, "quit", (), {}))
        self.worker.join(timeout)
