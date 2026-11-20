"""The engine in a process of its own, holding the unit for the whole session.

The desk sends one command at a time and gets a `Future` back. Commands run
in order. `stop()` sets a flag that a long command (a build, a listen)
checks between two writes, and clears the queue of commands not yet begun.

Running the engine in its own process means a failure of the audio driver
cannot take the desk down. When that process ends unexpectedly, the next one
gives back the player's preset when it opens, from the record the engine
left on disk.

`Host(local=...)` runs the same engine on a thread instead, for tests and
for the simulated unit.
"""

from concurrent.futures import Future
import multiprocessing
from queue import Empty
import threading
import traceback


def _engine(simulated, folder, physical_input):
    from lab.qc.engine import Engine
    from lab.qc.slots import Slots
    from lab.qc.unit import Unit

    if simulated:
        from lab.qc.sim import SimAudio, SimUnit

        device = SimUnit()
        unit = Unit(device, settle=.05, sleep=lambda seconds: None)
        audio = SimAudio(device)
        context = None
    else:
        from lab.qc import device as qc_device
        from lab.hearing.audio import Audio

        context = qc_device.connect(write=True)
        unit = Unit(context.__enter__())
        audio = Audio()
    return Engine(unit, Slots(unit, folder), audio, physical_input=physical_input), context


def _serve(commands, replies, stop, simulated, folder, physical_input):
    try:
        engine, context = _engine(simulated, folder, physical_input)
    except Exception as error:  # noqa: BLE001
        replies.put((None, False, f"{type(error).__name__}: {error}"))
        return
    replies.put((None, True, "ready"))
    try:
        while True:
            ticket, name, args, options = commands.get()
            if name == "quit":
                break
            if stop.is_set():
                replies.put((ticket, False, "Stopped before it began."))
                continue
            try:
                if name in ("show", "hear", "record"):
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
                replies.put((ticket, False, f"{error}" if str(error) else type(error).__name__,))
                traceback.print_exc()
    finally:
        try:
            engine.close()
        finally:
            if context is not None:
                context.__exit__(None, None, None)


class Host:
    def __init__(self, folder, *, simulated=False, local=False, physical_input=1):
        self.futures, self.ticket, self.lock = {}, 0, threading.Lock()
        if local:
            from queue import Queue

            self.commands, self.replies, self.stop_flag = Queue(), Queue(), threading.Event()
            self.worker = threading.Thread(target=_serve, daemon=True,
                                           args=(self.commands, self.replies, self.stop_flag, simulated, str(folder),
                                                 physical_input))
        else:
            context = multiprocessing.get_context("spawn")
            self.commands, self.replies, self.stop_flag = context.Queue(), context.Queue(), context.Event()
            self.worker = context.Process(target=_serve, daemon=True,
                                          args=(self.commands, self.replies, self.stop_flag, simulated, str(folder),
                                                physical_input))
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
                (self.ready.set_result if ok else self.ready.set_exception)(
                    value if ok else RuntimeError(value))
                if not ok:
                    return
                continue
            with self.lock:
                future = self.futures.pop(ticket, None)
                idle = not self.futures
            if idle:
                self.stop_flag.clear()
            if future is not None:
                future.set_result(value) if ok else future.set_exception(RuntimeError(value))

    def _fail_all(self, message):
        with self.lock:
            pending, self.futures = list(self.futures.values()), {}
        for future in pending:
            future.set_exception(RuntimeError(message))
        if not self.ready.done():
            self.ready.set_exception(RuntimeError(message))

    def call(self, name, *args, **options):
        future = Future()
        with self.lock:
            self.ticket += 1
            self.futures[self.ticket] = future
            ticket = self.ticket
        self.commands.put((ticket, name, args, options))
        return future

    def stop(self):
        """End the running command at its next safe point, and drop the ones waiting."""
        with self.lock:
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
