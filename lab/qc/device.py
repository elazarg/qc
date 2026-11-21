"""The connection to the Quad Cortex.

`connect(write=...)` opens the unit and yields the device object `unit.Unit`
drives (the library's `QuadCortex`, behind `Device`), and closes it after.

What this module knows about the unit and its library:

- Cortex Control has priority. It is looked for in the process list before
  the connection is made, again just before it is opened, and once more
  after, because it may be starting while the app connects. A refusal of the
  device because another program holds it is reported as Cortex Control's
  when Cortex Control is running.
- A connection that writes takes the device for itself; one that only reads
  shares it.
- Nothing is done with a unit whose firmware could not be read: the library
  picks the message schema from it.
- The library re-queues retained packets each time a reply matches, so every
  polled request grows its queue. `Device` clears the queue under the
  library's lock around each request that waits for a reply. A session that
  writes holds the device alone, so nothing else in the app wants those
  packets.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys

EDITOR = "Cortex Control.exe"
HANDOFF = ("Cortex Control is using the Quad Cortex. Close it to let Tone Lab use the unit. "
           "Your session stays open.")


class EditorActive(RuntimeError):
    """Cortex Control holds the unit. The app can still use local audio."""


def editor_running():
    """Whether Cortex Control is running, from the Windows process list. Never on another system."""
    if sys.platform != "win32":
        return False
    tasklist = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tasklist.exe"
    try:
        result = subprocess.run([str(tasklist), "/FI", f"IMAGENAME eq {EDITOR}", "/FO", "CSV", "/NH"],
                                capture_output=True, timeout=2.0, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return f'"{EDITOR.casefold()}"'.encode() in result.stdout.lower()


def _library(share):
    if os.environ.get("TONE_LAB_TESTING"):    # set by tests/isolated.py: no test opens the real unit
        raise RuntimeError("A test tried to open the real Quad Cortex. Use lab.qc.sim.SimUnit.")
    from qc_mcp.transport import QuadCortex

    return QuadCortex(share=share)


class Device:
    """The library's device object, with its request queue kept from growing.

    Every other attribute and call is the library's own. The library's lock
    is re-entrant, so a caller already holding it (`Unit.position`) can ask.
    """

    QUIET = ("read_state", "read_message", "get_setlist_position", "list_directory", "get_current_preset")

    def __init__(self, qc):
        object.__setattr__(self, "_qc", qc)

    def __getattr__(self, name):
        found = getattr(self._qc, name)
        if name in self.QUIET and callable(found):
            return self._cleared(found)
        return found

    def __setattr__(self, name, value):
        setattr(self._qc, name, value)

    def _cleared(self, call):
        def cleared(*args, **options):
            lock, pending = getattr(self._qc, "_io_lock", None), getattr(self._qc, "_pending", None)
            if lock is None or pending is None:
                return call(*args, **options)
            with lock:
                pending.clear()
                try:
                    return call(*args, **options)
                finally:
                    pending.clear()
        return cleared


@contextmanager
def connect(*, write=False, opener=_library, editor=editor_running):
    """The unit, open, as the object `unit.Unit` drives. Closed when the block ends.

    `opener(share)` makes the library's device object and `editor()` says
    whether Cortex Control is running; both are replaced in tests.
    """
    if editor():
        raise EditorActive(HANDOFF)
    qc = opener(not write)
    try:
        try:
            if editor():
                raise EditorActive(HANDOFF)
            qc.open()
        except (OSError, RuntimeError) as error:
            if isinstance(error, EditorActive):
                raise
            said = str(error).casefold()
            if ("exclusive" in said or "sharing violation" in said or "access is denied" in said) and editor():
                raise EditorActive(HANDOFF) from error
            raise
        if write and editor():   # It started while the connection was being made.
            raise EditorActive(HANDOFF)
        if not getattr(qc, "protocol_version_verified", False):
            raise RuntimeError("The Quad Cortex firmware could not be read, so Tone Lab will not write to it.")
        yield Device(qc)
    finally:
        qc.close()


def recording_channels(device, physical_input):
    """The USB channels to record from: (dry guitar, wet left, wet right).

    Which channels carry the dry input depends on the unit's USB Dry/Wet
    setting: with it on, inputs 1 and 2 arrive dry on channels 3 and 4 and
    the processed sound on 1 and 2; with it off, the other way round.
    """
    if physical_input not in (1, 2):
        raise ValueError("Choose guitar input 1 or 2.")
    io = device.read_message("IOSettings") or device.read_message("IOSettings")
    if (io is None or not io.HasField("settings") or not io.settings.HasField("usb_port")
            or io.settings.usb_port.dry_wet not in (0, 1)):
        raise RuntimeError("Could not read the Quad Cortex USB Dry/Wet setting. Check the connection and try again.")
    return (physical_input + 2, 1, 2) if io.settings.usb_port.dry_wet else (physical_input, 3, 4)
