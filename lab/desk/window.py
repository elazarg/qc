"""The desk: the window over `Studio`. It draws the state and turns clicks into actions.

Run it with `python -m lab` (the Quad Cortex) or `python -m lab --simulated`
(no hardware). Two sounds, the input, one line that speaks, one place to ask,
and panels that open when called. Nothing here decides anything; `studio.py`
does.

Everything happens in this one window: no dialogs, no pop-up windows or
menus, no keyboard shortcuts. Choices are buttons, names are typed in place,
and what is worth knowing once is behind a "?" (`lab.desk.hints`). The side
tools are shown in the place of the side panel, one at a time (`Desk.show`),
with "‹ Back" to return to the panel they were opened from. Each is made once
and kept, so it comes back as it was left.

The top row is there on every screen. At its left: the learning symbol, which
puts the learning screen (`lab.learner.screen`: the Guitar Learner and Teach a
control) in the place of everything below the row and brings the desk back as
it was; and the instrument chooser (`lab.desk.chooser`), which says which
instrument is played and changes it for the whole app at once. At its right,
while the desk is on show, the names of the panels.

What is drawn again, and when: the tiles and the lines on every change of
state; the round's chips, the scene chips and the open panel only when what
they show has changed, and never while something in the panel is being
dragged.
"""

import json
from pathlib import Path
import sys
import tkinter as tk
import traceback

from lab.sound import knobs as knob_table
from lab.hearing.audio import read_wave
from lab.knowledge import REPO, home
from lab.hearing.measure import summary
from lab.desk.chooser import InstrumentChooser
from lab.desk.drag import Drags
from lab.desk.phrase_strip import PhraseStrip
from lab.desk.scales import Scales
from lab.tools.common import flow
from lab.desk.difference import against, aim_against, misses
from lab.desk.studio import COPY_REQUEST
from lab.desk.hints import hint, named
from lab.desk.pictures import learning_symbol
from lab.desk.studio import RATE, Studio
from lab.learner.seat import LearnerSeat  # noqa: F401  (the desk makes one; tests import it from here)
from lab.desk.theme import (ACCENT, DARK, DIM, FAULT, FONT, GLYPH, INK, LIVE, PAPER, RULE, SIDE, SOFT, SUBTLE,
                            TILE, icon_font, mix)

PANELS = ("Shape", "Chain", "Difference", "Phrase", "Sounds", "History", "Guitar", "More")
SIDED = ("Shape", "Chain", "Sounds")       # panels that act on or show the sound on the selected side
SCROLLED = ("Chain", "Difference", "Phrase", "Guitar", "More")      # panels built into a part that scrolls
RECORD_SECONDS = 8.
SHORTEST_LOOP = RATE // 2       # a loop is at least half a second
PLACEHOLDER = "Ask for a sound, a song, or a change"
SHOWN_AT_MOST = 400     # rows of IRs or captures listed at once; the filter reaches the rest
PAD_SIZE = 200      # the Brightness x Texture pad, in pixels a side


def pad_point(across, up, size=PAD_SIZE):
    """Where knob positions (-1 to 1 each) sit on the pad: across from the left edge, up from the bottom."""
    return (across + 1) / 2 * size, (1 - up) / 2 * size


def pad_values(x, y, size=PAD_SIZE):
    """The knob positions at a point on the pad, kept on the pad and on steps of .05."""
    def position(fraction):
        return round(round(max(-1., min(1., fraction)) * 20) / 20, 2)

    return position(2 * x / size - 1), position(1 - 2 * y / size)


def label(parent, text="", *, size=10, color=INK, bold=False, wrap=0):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=(FONT, size, "bold" if bold else "normal"),
                    justify="left", anchor="w", wraplength=wrap)


def button(parent, text, action, *, dark=True):
    return tk.Button(parent, text=text, command=action, relief="flat", borderwidth=0, padx=12, pady=6,
                     cursor="hand2", font=(FONT, 10, "bold"), bg=TILE if dark else "#252834", fg=INK,
                     activebackground=RULE, activeforeground=INK)


def link(parent, text, action, *, color=SOFT):
    """Words that are clicked."""
    found = tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=(FONT, 9), cursor="hand2", padx=8)
    found.bind("<ButtonRelease-1>", lambda _: action())
    return found


def history_rows(folder):
    """Every sound kept, newest first, as (id, name, where it came from, whether it is saved on the unit). Read
    from the files as they are, without building the chains: the list is long and is drawn often."""
    rows = []
    for path in (Path(folder) / "sounds").glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            rows.append((str(record.get("created", "")), record["id"], str(record["name"]),
                         str((record.get("origin") or {}).get("kind", "")), bool(record.get("saved"))))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return [row[1:] for row in sorted(rows, reverse=True)]


def loop_edges(edges, at, shortest=SHORTEST_LOOP):
    """The loop's edges after the nearer one is dragged to `at` (all in samples). An edge that would leave less
    than `shortest` between them stays where it is."""
    start, end = edges
    if abs(at - start) < abs(at - end):
        start = at
    else:
        end = at
    return (start, end) if end - start >= shortest else edges


class Desk:
    def __init__(self, root, studio, door=None):
        """`door` is the socket `claim_door` bound, on which a second launch knocks; None for a desk without one
        (a simulated unit, a test)."""
        self.root, self.studio = root, studio
        self.panel = None
        self.into = None                # what the open panel is built into: the tray, or a part of it that scrolls
        self.learning = None            # the learning screen, once the learning symbol was pressed
        self.learning_on = False        # whether it is on show, in the place of everything below the top row
        self.instrument_shown = None    # the instrument the desk last drew for
        self.panel_key = None           # what the open panel was drawn from; it is redrawn only when that changes
        self.strip_key = self.scene_key = None      # the same for the round's chips and the scene chips
        self.dragging = False           # something in the panel is being dragged: the panel is left as it is
        self.expanded = set()           # blocks whose named settings are shown in the Chain panel
        self.show_nano = False
        self.show_measured = False      # whether the Chain panel lists what was measured, line by line
        self.all_takes = False          # whether the Phrase panel lists every take, or the newest eight
        self.sounds_kind = "Presets"    # which list the Sounds panel shows
        self.capture_version = 2        # what an unlabelled capture is taken to be
        self.record_setting = None      # the pickup setting the next take is played on
        self.record_gesture = None      # what is played in it, when it is one of the gestures settings are compared on
        self.gestures_shown = None      # what the Guitar panel last showed of gesture listening
        self.tools = {}                 # the side tools made so far, by title (`lab.tools.common.Tool.open`)
        self.hosted = None              # the tool on show in the side panel's place
        self.sounds_query = ""          # what the Sounds panel's presets are filtered by
        self.preset_more = False        # whether the Sounds panel shows what else can be done with a preset
        self.reference_scene = "Default scene"      # the scene a preset is captured in as a reference
        self.contrast = None            # what "Measure A and B now" found, and for which sounds and phrase
        self.save_target = None         # (sound id, knob positions) the name being typed is for
        self.seat = None                # the Guitar Learner's seat, once something of the learner was opened
        self.clock, self.door = None, door      # the `after` of `tick`; the socket a second launch knocks on
        self.drawing_fault = None       # the last fault while drawing, so that it is logged once
        self.play_when_free = None      # what to play once gesture listening has let go of the audio input
        self.closing = False
        root.report_callback_exception = self.failed
        root.title("Tone Lab")
        root.configure(bg=PAPER)
        try:
            root.iconbitmap(default=str(REPO / "ToneLab.ico"))   # this window and every one it opens
        except tk.TclError:
            pass
        root.geometry("1100x680")
        root.minsize(980, 620)
        self.build()
        studio.listeners.append(lambda state: self.draw())
        root.protocol("WM_DELETE_WINDOW", self.close)
        # The clock stops with the window, so nothing is called on a window that is gone.
        root.bind("<Destroy>", lambda event: event.widget is root and self.clock and root.after_cancel(self.clock),
                  add="+")
        try:
            self.come_back()
        except Exception as error:  # noqa: BLE001  whatever was left behind, the desk opens
            studio.set_aside(error)
            self.panel = None
            self.tray.grid_remove()
            self.draw()
        studio.open()
        self.tick()

    def come_back(self):
        """Put back what the last session left: A and B, the phrase and its loop, the open panel."""
        session = self.studio.restore() or {}
        recorded = self.studio.recorded_phrases()
        kept = session.get("phrase") if isinstance(session.get("phrase"), dict) else {}
        item = next((item for item in recorded if item["id"] == kept.get("id")), None)
        if item is not None:
            self.choose_recorded(item, kept.get("start"), kept.get("end"))
        elif recorded:
            self.choose_recorded(recorded[0])
        panel = self.studio.desk_session.get("panel")
        if panel in PANELS:
            self.toggle(panel)
        self.paint()

    # ---- building ----

    def build(self):
        page = self.page = tk.Frame(self.root, bg=PAPER, padx=20, pady=16)
        page.pack(fill="both", expand=True)
        top = tk.Frame(page, bg=PAPER)
        top.pack(fill="x")
        # The learning symbol: pressed, the learning screen takes the place of everything below this row; pressed
        # again, the desk is back as it was. It is lit while the learning screen is on.
        self.symbols = {on: learning_symbol(self.root, 30, 25, paper=TILE if on else PAPER, ink=ACCENT if on else SOFT)
                        for on in (False, True)}
        self.learn_symbol = tk.Label(top, image=self.symbols[False], bg=PAPER, cursor="hand2", width=40, height=34,
                                     borderwidth=0, highlightthickness=1, highlightbackground=RULE)
        self.learn_symbol.pack(side="left", padx=(0, 8))
        self.learn_symbol.bind("<ButtonRelease-1>", lambda _: self.toggle_learning())
        named(self.learn_symbol, lambda: "Back to the desk" if self.learning_on else "Learn the guitar")
        self.chooser = InstrumentChooser(top, instruments=self.studio.store.instruments,
                                         current=lambda: self.studio.state.instrument, choose=self.choose_instrument)
        self.chooser.pack(side="left")
        # Where a suggestion of the instrument would be shown ("Are you using the Les Paul?", with a click to
        # take it): beside the chooser. Nothing guesses yet, so the frame holds nothing.
        self.suggestion = tk.Frame(top, bg=PAPER)
        self.suggestion.pack(side="left", padx=(8, 0))
        self.names = tk.Frame(top, bg=PAPER)        # the panels' names: not shown while the learning screen is on
        self.names.pack(side="right")
        self.openers = {}               # the panels' names, by name: the open one is lit
        for name in PANELS[::-1]:
            opener = tk.Label(self.names, text=name, bg=PAPER, fg=SUBTLE, font=(FONT, 10), cursor="hand2", padx=8)
            opener.pack(side="right")
            opener.bind("<ButtonRelease-1>", lambda _, name=name: self.toggle(name))
            self.openers[name] = opener

        body = self.body = tk.Frame(page, bg=PAPER)
        body.pack(fill="both", expand=True, pady=(12, 0))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        main = tk.Frame(body, bg=PAPER)
        main.grid(row=0, column=0, sticky="nsew")
        self.tray = tk.Frame(body, bg=DARK, width=430, padx=12, pady=10)
        self.tray.grid_propagate(False)
        self.tray.pack_propagate(False)        # one width for every panel
        # Where a side tool is shown: the tray's place and size. Its frames are not the tray's children, which
        # are destroyed whenever a panel is drawn again.
        self.side = tk.Frame(body, bg=DARK, width=430, padx=12, pady=10)
        self.side.grid_propagate(False)
        self.side.pack_propagate(False)
        head = tk.Frame(self.side, bg=DARK)
        head.pack(fill="x", pady=(0, 6))
        self.side_title = label(head, size=11, bold=True)
        self.side_title.pack(side="left")
        link(head, "‹ Back", self.back, color=ACCENT).pack(side="right")

        pair = tk.Frame(main, bg=PAPER)
        pair.pack(fill="both", expand=True)
        pair.columnconfigure((0, 2), weight=1, uniform="pair")
        pair.rowconfigure(0, weight=1)
        self.tiles = {}
        self.scales, self.blocks = {}, {}           # by side: the aim against what was heard; the chain's blocks
        self.blocks_key = {}                        # by side: what the blocks were drawn from
        # Dragging onto a tile: the other tile, a design of the round, a sound from History, an IR or a capture.
        self.drags = Drags(self.root, targets={}, dropped=self.dropped, lit=self.light_targets)
        for column, side in ((0, "a"), (2, "b")):
            tile = tk.Frame(pair, bg=DARK, padx=16, pady=13, highlightthickness=2, highlightbackground=DARK,
                            cursor="hand2")
            tile.grid(row=0, column=column, sticky="nsew")
            # At the top: whether it plays, and the player's marks, a heart and a cross, filled when given.
            top = tk.Frame(tile, bg=DARK)
            top.pack(side="top", fill="x")
            air = label(top, size=8, color=LIVE, bold=True)
            air.pack(side="left")
            marks = tk.Frame(top, bg=DARK)
            marks.pack(side="right")
            for word in ("keeper", "not this"):
                chip = tk.Label(marks, text=GLYPH["keep" if word == "keeper" else "not this"], font=icon_font(13),
                                padx=5, pady=2, cursor="hand2", bg=DARK, fg=SUBTLE)
                chip.pack(side="left")
                chip.bind("<ButtonRelease-1>", lambda _, side=side, word=word: self.studio.mark(word, side))
                named(chip, "Keep" if word == "keeper" else "Not this")
            badge = label(tile, side.upper(), size=34, color=SIDE[side], bold=True)
            name = label(tile, size=14, bold=True, wrap=320)
            blocks = tk.Frame(tile, bg=DARK)
            scales = Scales(tile)
            named(scales, lambda scales=scales: scales.words)
            caption = label(tile, size=9, color=SUBTLE, wrap=320)
            caption.pack(side="bottom", fill="x")
            scales.pack(side="bottom", fill="x", pady=(6, 2))
            blocks.pack(side="bottom", fill="x", pady=(4, 0))
            name.pack(side="bottom", fill="x")
            badge.pack(side="bottom", fill="x")
            self.scales[side], self.blocks[side] = scales, blocks
            for widget in (tile, badge, name, caption, air, top, scales):
                widget.bind("<ButtonRelease-1>", self.drags.click(lambda side=side: self.studio.select(side)))
            for widget in (tile, badge, name, caption):
                self.drags.source(widget, lambda event, side=side: ("side", side) if self.studio.sound(side)
                                  else None, self.dragged_name)
            # A name or a caption longer than the tile is wide goes on to the next line, however wide the tile is.
            tile.bind("<Configure>", lambda event, name=name, caption=caption, blocks=blocks: (
                name.configure(wraplength=max(100, event.width - 40)),
                caption.configure(wraplength=max(100, event.width - 40)),
                blocks.winfo_children() and flow(blocks, blocks.winfo_children(), width=max(160, event.width - 40))))
            self.tiles[side] = (tile, name, caption, air, marks, badge)
        self.drags.targets.update({side: self.tiles[side][0] for side in ("a", "b")})
        # Between the tiles: B onto A, comparing blind, and undo, as symbols that say their names on hover.
        between = tk.Frame(pair, bg=PAPER, width=40)
        between.grid(row=0, column=1, sticky="ns", padx=4)
        middle = tk.Frame(between, bg=PAPER)
        middle.place(relx=.5, rely=.5, anchor="center")

        def symbol(glyph, action, words):
            found = tk.Label(middle, text=glyph, font=icon_font(15), bg=PAPER, fg=SOFT, cursor="hand2", padx=6,
                             pady=6)
            found.pack(pady=4)
            found.bind("<ButtonRelease-1>", lambda _: action())
            named(found, words)
            return found

        self.copy_link = symbol(GLYPH["copy"], lambda: self.studio.copy_to_a(),
                                lambda: self.unable("copy") or "Copy B to A (or drag B onto A)")
        self.blind_link = symbol(GLYPH["blind"], lambda: self.studio.blind(),
                                 lambda: self.unable("blind") or ("Show which was which" if self.studio.state.blind
                                                                  else "Compare blind"))
        self.undo_link = symbol(GLYPH["undo"], lambda: self.studio.undo(), self.studio.undo_words)

        # Shown while the Levels tool's offset or mute is on: nothing else on the desk would say so.
        self.level_row = tk.Frame(main, bg=PAPER)
        self.level_note = label(self.level_row, size=9, color=SUBTLE)
        self.level_note.pack(side="left")
        link(self.level_row, "Back to the desk's own level", self.clear_listening).pack(side="left")
        self.scene_row = tk.Frame(main, bg=PAPER)
        self.scene_row.pack(fill="x", pady=(8, 0))
        self.strip = tk.Frame(main, bg=PAPER)
        self.strip.pack(fill="x", pady=(10, 0))
        self.chips = tk.Frame(self.strip, bg=PAPER)
        self.chips.pack(fill="x")
        self.chips.bind("<Configure>", lambda event: self.flow_chips(event.width), add="+")
        self.strip_note = label(self.strip, size=9, color=SUBTLE, wrap=600)
        self.strip_note.pack(fill="x", pady=(4, 0))

        row = tk.Frame(main, bg=PAPER)
        row.pack(fill="x", pady=(12, 0))
        # Placed in the order of what must stay when the row is narrow (a panel open in the smallest window):
        # saving first, the phrase last, which is then drawn narrower.
        self.save_button = button(row, "Save to QC", self.save)
        self.save_button.pack(side="right", padx=(8, 0))
        named(self.save_button, lambda: self.unable("save") or "Save the selected sound to the Tone Lab setlist")
        self.guitar = button(row, "Guitar", lambda: self.toggle_play("guitar"))
        self.guitar.pack(side="left")
        named(self.guitar, lambda: self.unable("guitar") or "Play the guitar through the selected sound")
        self.loop = button(row, GLYPH["loop"], lambda: self.toggle_play("phrase"))
        self.loop.configure(font=icon_font(13), padx=12)
        self.loop.pack(side="left", padx=(2, 0))
        named(self.loop, lambda: self.unable("phrase") or "Loop the phrase through the selected sound")
        # The phrase itself: its shape, the part that loops and where the loop is now. A click opens the Phrase
        # panel; the dot beside it records a new one on the pickup setting last used.
        self.record_symbol = tk.Label(row, text=GLYPH["record"], font=icon_font(13), bg=PAPER, fg=SOFT,
                                      cursor="hand2", padx=6)
        self.record_symbol.pack(side="right")
        self.record_symbol.bind("<ButtonRelease-1>", lambda _: self.record_now())
        named(self.record_symbol, self.record_words)
        self.phrase_strip = PhraseStrip(row, open_panel=lambda: self.toggle("Phrase"))
        self.phrase_strip.pack(side="left", fill="x", expand=True, padx=(10, 2))
        named(self.phrase_strip, self.phrase_words)
        # Naming a save, in place: shown when Save to QC is pressed.
        self.save_row = tk.Frame(main, bg=PAPER)
        label(self.save_row, "Save as", size=9, color=SOFT).pack(side="left")
        self.save_name = tk.Entry(self.save_row, font=(FONT, 11), bg=TILE, fg=INK, relief="flat", insertbackground=INK,
                                  width=26)
        self.save_name.pack(side="left", padx=8, ipady=4)
        self.save_name.bind("<Return>", lambda _: self.saved())
        button(self.save_row, "Save", self.saved).pack(side="left")
        link(self.save_row, "Cancel", self.save_row.pack_forget).pack(side="left")
        self.save_row_after = row

        self.line = label(main, size=10, color=INK, wrap=640)
        self.line.pack(fill="x", pady=(8, 0))
        # What is being worked on, with Cancel while a request is with the assistant: just above the line it was
        # asked in, where the eyes are. Its height is kept whether it says anything or not, so that nothing above
        # it moves when work begins and ends. Cancel is placed first, so that a long line never pushes it out.
        self.working_row = tk.Frame(main, bg=PAPER, height=24)
        self.working_row.pack_propagate(False)
        self.working_row.pack(fill="x", pady=(2, 2))
        self.cancel_link = link(self.working_row, "Cancel", lambda: self.studio.cancel(), color=ACCENT)
        self.working = label(self.working_row, size=9, color=SOFT)
        self.working.pack(side="left", fill="x")
        # After a remark, while nothing is being worked on: the next remedy players try, to click instead of
        # typing the same words again.
        self.next_link = link(self.working_row, "", lambda: self.studio.try_next_remedy(), color=ACCENT)
        self.working_text, self.working_since = "", 0.
        self.line_said, self.line_moment = None, None   # the status last drawn, and what was on the desk then
        # Lines that say something follow the width of their column: a panel open narrows it.
        main.bind("<Configure>", lambda event: self.follow_width(event.width), add="+")
        ask = tk.Frame(main, bg=DARK, highlightthickness=1, highlightbackground=RULE)
        ask.pack(fill="x")
        self.request = tk.Entry(ask, font=(FONT, 12), bg=DARK, fg=INK, relief="flat", insertbackground=INK)
        self.request.pack(side="left", fill="x", expand=True, ipady=9, padx=12)
        self.request.bind("<Return>", lambda _: self.ask())
        # What the line is for, in it, while nothing is typed: the line has no name of its own. A click on the
        # words is a click in the line.
        self.placeholder = tk.Label(ask, text=PLACEHOLDER, bg=DARK, fg=DIM, font=(FONT, 12), cursor="xterm")
        self.placeholder.bind("<ButtonRelease-1>", lambda _: self.request.focus_set())
        self.show_placeholder()
        # What is under the tiles is given its room first, from the bottom up, so that in a small window the
        # tiles give way and the ask line, the buttons and the round's chips are never squeezed.
        for widget in (ask, self.working_row, self.line, row, self.strip, self.scene_row):
            widget.pack_configure(side="bottom", before=pair)

    # ---- drawing ----

    def draw(self):
        """Draw the state. A fault in drawing is logged once and said on the line; it never reaches the action
        that changed the state."""
        try:
            self.paint()
        except Exception as error:  # noqa: BLE001
            said = f"{type(error).__name__}: {error}"
            if said != self.drawing_fault:
                self.drawing_fault = said
                self.studio.log_error(error, traceback.format_exc())
            self.line.configure(text=f"Something went wrong drawing the desk: {error}. The details are in "
                                     "errors.log in the data folder.", fg=FAULT)

    def paint(self):
        studio, state = self.studio, self.studio.state
        sounds = {side: studio.sound(side) for side in ("a", "b")}
        if state.instrument != self.instrument_shown:
            self.instrument_changed()
        # While the learning screen is on, what the desk is working on and its status line are said on that
        # screen's own line.
        self.show_working()
        if self.learning_on:
            self.learning.say(state.status, state.working)
        # Cancel is there for as long as a request is with the assistant, whatever else the working line says.
        if studio.cancellable() and not self.learning_on:
            if not self.cancel_link.winfo_manager():
                self.cancel_link.pack(side="right", before=self.working)
        else:
            self.cancel_link.pack_forget()
        for side in ("a", "b"):
            tile, name, caption, air, marks, badge = self.tiles[side]
            sound = sounds[side]
            hidden = state.blind and sound is not None
            name.configure(text="?" if hidden else (sound.name if sound else "Empty"))
            caption.configure(text="Hidden while you compare" if hidden else self.caption(sound, side))
            selected = state.side == side
            on_air = state.mode and selected
            # The tile first: everything on it takes its colour, the letter and the marks included.
            tile.configure(highlightbackground=SIDE[side] if selected else DARK,
                           bg=mix(SIDE[side], DARK, .14) if on_air else DARK)
            for widget in (name, caption, air, marks, badge, air.master, self.blocks[side]):
                widget.configure(bg=tile["bg"])
            # The heart and the cross stay while comparing blind (choosing shows the names); a mark already given
            # is not shown then, since it would tell which sound is which. A mark given is drawn filled.
            for chip, word in zip(marks.winfo_children(), ("keeper", "not this")):
                marked = sound is not None and not hidden and word in sound.marks
                chip.configure(bg=tile["bg"], fg=SIDE[side] if marked else SUBTLE,
                               text=GLYPH["kept"] if word == "keeper" and marked
                               else GLYPH["keep" if word == "keeper" else "not this"])
            if sound is None:
                marks.pack_forget()
            elif not marks.winfo_manager():
                marks.pack(side="right")
            # Lit once the unit confirmed it plays; until then, that it is on its way.
            air.configure(text=(f"● {state.mode}" if studio.sounding == (side, state.mode) else "○ getting ready")
                          if on_air else "")
            # The chain's blocks, and the aim against what was heard: not while names are hidden, since they
            # would tell which is which.
            self.draw_blocks(side, None if hidden else sound, None if hidden else sounds["b" if side == "a" else "a"])
            heard = None if hidden or sound is None or studio.unheard(sound) else sound.heard
            self.scales[side].show(None if hidden else self.aim_of(sound), heard, SIDE[side])
        self.paint_line(state)
        # A control that cannot act now is dimmed, and says why while the pointer rests on it; pressing it says
        # so too. While something else has the audio input, the two play buttons are dimmed the same way.
        for widget, mode in ((self.guitar, "guitar"), (self.loop, "phrase")):
            widget.configure(bg=mix(LIVE, TILE, .4) if state.mode == mode else TILE,
                             fg=DIM if self.unable(mode) else INK)
        self.save_button.configure(fg=DIM if self.unable("save") else INK)
        self.copy_link.configure(fg=DIM if self.unable("copy") else SOFT)
        self.blind_link.configure(fg=DIM if self.unable("blind") else SIDE["b"] if state.blind else SOFT,
                                  text=GLYPH["shown"] if state.blind else GLYPH["blind"])
        self.undo_link.configure(fg=SOFT if studio.history else DIM)
        if studio.listening is not None:
            from lab.tools.levels import reading

            self.level_note.configure(text=f"Volume: {reading(studio.listening)}")
            if not self.level_row.winfo_manager():
                self.level_row.pack(side="bottom", fill="x", pady=(6, 0), after=self.scene_row)
        elif self.level_row.winfo_manager():
            self.level_row.pack_forget()
        since = studio.loop_since[1] if studio.loop_since and state.mode == "phrase" else None
        self.phrase_strip.show(studio.phrase_whole if studio.phrase else None, since)
        self.record_symbol.configure(fg=DIM if self.unable("record") else SOFT)
        self.draw_strip(sounds["b"])
        self.draw_scenes()
        if self.panel and self.hosted is None and not self.dragging and self.drawn_from(sounds) != self.panel_key:
            self.fill_panel()

    def unable(self, what):
        """Why a control of the desk cannot act now, in a few words, or "" when it can. The control is dimmed
        while it cannot, and says this while the pointer rests on it."""
        studio, state = self.studio, self.studio.state
        a, b, chosen = studio.sound("a"), studio.sound("b"), studio.sound(state.side)
        if what in ("guitar", "phrase", "record") and studio.audio_held_by \
                and not (what == "guitar" and studio.audio_held_by == "gestures"):
            return studio.held_words()
        if what in ("guitar", "phrase") and a is None and b is None:
            return "Nothing is on A or B yet"
        if what == "phrase" and studio.phrase is None:
            return "Record or choose a phrase first"
        if what == "save" and chosen is None:
            return f"Nothing is on {state.side.upper()} to save"
        if what == "copy" and b is None:
            return "Nothing is on B to copy"
        if what == "blind" and not state.blind and (a is None or b is None):
            return "Blind comparison needs a sound on both A and B"
        return ""

    def aim_of(self, sound):
        """The aim of the round on show, when `sound` is one of its designs or a copy of one; else None."""
        found = self.studio.round() if sound is not None else None
        if not found:
            return None
        ids = {candidate["sound"] for candidate in found["candidates"]}
        return found["aim"] if sound.id in ids or sound.parent in ids else None

    def draw_blocks(self, side, sound, other):
        """The chain of the sound on a side as a row of its blocks, each named; a block the other side does not
        have as it is set here is outlined in this side's colour. A click on one opens the Chain panel."""
        key = None if sound is None else (sound.id, other.id if other else None, sound.chain.digest(),
                                          other.chain.digest() if other else None)
        holder = self.blocks[side]
        if key == self.blocks_key.get(side):
            return
        self.blocks_key[side] = key
        for child in holder.winfo_children():
            child.destroy()
        if sound is None:
            return
        theirs = {(r, column): block for r, column, block in other.chain.blocks} if other else {}
        chips = []
        for r, column, block in sound.chain.blocks:
            differs = other is not None and theirs.get((r, column)) != block
            chip = tk.Label(holder, text=block.name if len(block.name) <= 22 else block.name[:21] + "…",
                            font=(FONT, 8), bg=TILE, fg=INK if differs else SOFT, padx=5, pady=1, cursor="hand2",
                            highlightthickness=1, highlightbackground=SIDE[side] if differs else TILE)
            chip.bind("<ButtonRelease-1>", lambda _, side=side: (self.studio.select(side),
                                                                 self.panel != "Chain" and self.toggle("Chain")))
            chips.append(chip)
        holder.update_idletasks()
        flow(holder, chips, width=max(160, self.tiles[side][0].winfo_width() - 40), gap=4)

    def dragged_name(self, what):
        """The few words that follow the pointer while something is dragged."""
        kind, value = what
        if kind == "side":
            sound = self.studio.sound(value)
            return "?" if self.studio.state.blind else (sound.name if sound else value.upper())
        if kind in ("design", "sound"):
            try:
                return self.studio.store.sound(value).name
            except Exception:  # noqa: BLE001  a sound gone meanwhile: it is dropped as nothing
                return "…"
        return value.name

    def light_targets(self, on):
        """While something is dragged, both tiles show they take it; afterwards they are drawn as they are."""
        if on:
            for side in ("a", "b"):
                self.tiles[side][0].configure(highlightbackground=SIDE[side])
        else:
            self.draw()

    def dropped(self, side, what):
        """Something was let go over a tile."""
        studio = self.studio
        kind, value = what
        if kind == "side":
            if value != side and studio.sound(value) is not None and not studio.state.blind:
                studio.copy_over(studio.sound(value), side)
        elif kind == "design":
            studio.copy_over(studio.store.sound(value), side)
        elif kind == "sound":
            studio.put(side, studio.store.sound(value))
        elif kind == "asset":
            studio.select(side)
            studio.use_asset(value, has_cab=getattr(value, "has_cab", None),
                             version=None if value.kind == "ir" else value.capture_version or self.capture_version)

    def phrase_words(self):
        """What the phrase strip says while the pointer rests on it."""
        studio = self.studio
        if not studio.phrase:
            return "No phrase: record one with the dot, or choose one in the Phrase panel"
        item = next((item for item in studio.recorded_phrases() if item["id"] == studio.phrase[0]), None)
        parts = [item.get("instrument") if item and item.get("instrument") != studio.state.instrument else None,
                 item.get("setting") if item else None]
        whole = studio.phrase_whole
        loop = f" · loop {whole[1] / RATE:.1f}–{whole[2] / RATE:.1f} s of {len(whole[0]) / RATE:.1f} s" if whole else ""
        return " · ".join(["Phrase", *filter(None, parts)]) + loop

    def record_words(self):
        why = self.unable("record")
        if why:
            return why
        setting = self.remembered_setting()
        return (f"Record {RECORD_SECONDS:g} s on {setting}" if setting
                else "Record a phrase: choose the pickup setting in the Phrase panel")

    def remembered_setting(self):
        """The pickup setting the player last recorded on, on this instrument, or None."""
        return (self.studio.desk_session.get("settings") or {}).get(self.studio.state.instrument)

    def record_now(self):
        """The dot by the phrase: record on the setting last used. With none yet, the Phrase panel opens, where
        it is chosen."""
        if self.unable("record"):
            self.studio.say(self.unable("record"))
            return
        setting = self.remembered_setting()
        if setting is None:
            if self.panel != "Phrase":
                self.toggle("Phrase")
            self.studio.say("Choose the pickup setting, then record.")
            return
        self.studio.record(setting, RECORD_SECONDS, gesture=self.record_gesture)

    def paint_line(self, state):
        """The status line in one of three looks: a fault in its own colour; what was just said, plain; and,
        once something has happened on the desk since it was said, dimmed, so that an old remark does not read
        as news."""
        moment = (state.a, state.b, state.side, state.mode, state.scene, state.round, state.blind, state.instrument)
        if state.status != self.line_said:
            self.line_said, self.line_moment = state.status, moment
        old = moment != self.line_moment
        color = FAULT if state.status_kind == "fault" else SUBTLE if old else INK
        self.line.configure(text=state.status, fg=color)

    def follow_width(self, width):
        """The lines under the tiles wrap at the width their column has now, not at a width fixed for the
        widest window: with a panel open the column is narrower, and a line cut at its edge loses its end."""
        for line in (self.line, self.strip_note):
            line.configure(wraplength=max(200, width - 8))

    def show_working(self):
        """The working line: what is being done, and for how long once it has taken a few seconds, so that a
        request that is slow can be told from one that is stuck. Called on every change and by the clock."""
        import time

        text = "" if self.learning_on else self.studio.state.working
        if text != self.working_text:
            self.working_text, self.working_since = text, time.monotonic()
        seconds = time.monotonic() - self.working_since
        shown = f"{text}  {seconds:.0f} s" if text and seconds >= 3 else text
        if self.working.cget("text") != shown:
            self.working.configure(text=shown)
        following = None if text else self.studio.next_remedy()
        if following:
            self.next_link.configure(text=f"Next: {following}  ›")
            if not self.next_link.winfo_manager():
                self.next_link.pack(side="left", padx=0)
        elif self.next_link.winfo_manager():
            self.next_link.pack_forget()

    def drawn_from(self, sounds=None):
        """What the open panel shows depends on: the sounds on show, what is known of them, the round, the scene."""
        studio, state = self.studio, self.studio.state
        sides = []
        for side in ("a", "b"):
            sound = sounds[side] if sounds else studio.sound(side)
            sides.append(None if sound is None else (sound.id, json.dumps(sound.heard, sort_keys=True, default=str),
                                                     json.dumps(sound.knobs, sort_keys=True), tuple(sound.marks)))
        return (self.panel, state.side, state.round, state.blind, state.scene, state.song, state.instrument,
                tuple(sides), self.show_nano, self.show_measured, tuple(sorted(self.expanded)), studio.phrase_key(),
                tuple(sorted(studio.hearing_base)), studio.profile_version if self.panel == "Guitar" else None)

    def caption(self, sound, side="b"):
        """The line under a sound's name. On B, first, what B has that A has not: what the player listens for."""
        if sound is None:
            return ""           # the ask line says what to do, once, instead of both tiles
        from lab.learner.dataset import suggested_setup

        setup = suggested_setup(sound.origin.get("guitar_setup"), self.studio.state.instrument)
        parts = [setup] if setup else []
        if side == "b":
            differs = against(sound, self.studio.sound("a"), "A", blocks=False)     # the blocks are outlined
            if differs:
                parts.insert(0, differs)
        origin = sound.origin.get("kind")
        request = sound.origin.get("request") or ""
        if origin == "preset":
            parts.append("Saved on the Quad Cortex")
        elif origin == "revised" and request and request != COPY_REQUEST:      # a copy says nothing new
            parts.append("Revised: " + request[:60])
        if self.studio.unheard(sound):
            parts.append("could not be listened to")      # what was heard is on the scales above
        if sound.knobs:
            parts.append("shaped")
        if sound.saved:
            parts.append(f"saved as {sound.saved['name']}")
        return " · ".join(parts)

    def draw_scenes(self):
        scenes = self.studio.scenes()
        key = (tuple(scenes), self.studio.state.scene, self.studio.state.side)
        if key == self.scene_key:
            return
        self.scene_key = None               # until they are drawn whole
        for child in self.scene_row.winfo_children():
            child.destroy()
        if scenes:
            label(self.scene_row, "Scene", size=9, color=DIM).pack(side="left", padx=(0, 6))
        for scene, name in scenes:
            chosen = scene == self.studio.state.scene
            chip = tk.Label(self.scene_row, text=f"{'ABCDEFGH'[scene]}{'  ' + name if name else ''}", padx=8, pady=3,
                            cursor="hand2", font=(FONT, 9, "bold" if chosen else "normal"),
                            bg=mix(SIDE[self.studio.state.side], DARK, .3) if chosen else TILE,
                            fg=INK if chosen else SOFT)
            chip.pack(side="left", padx=(0, 4))
            chip.bind("<ButtonRelease-1>", lambda _, scene=scene: self.studio.scene(scene))
        self.scene_key = key

    def draw_strip(self, on_b=None):
        """The round on show: its three designs as chips, the next and the earlier round, and what it aims for."""
        studio = self.studio
        found = None if studio.state.blind else studio.round()
        # The chips are made again only when what they show has changed: a chip that is replaced between the
        # press and the release of a click loses the click.
        key = found and (found["id"], found.get("next"), found.get("previous"), studio.state.b, studio.ear.hearing,
                         tuple((summary(sound.heard), bool(studio.unheard(sound))) for sound in
                               (studio.store.sound(item["sound"]) for item in found["candidates"])))
        if key == self.strip_key:
            return
        self.strip_key = None               # until they are drawn whole: a fault on the way is met again, not hidden
        for child in self.chips.winfo_children():
            child.destroy()
        self.strip_note.configure(text="")
        if not found:
            self.strip_key = key
            return
        if found.get("previous"):
            back = tk.Label(self.chips, text="‹", padx=6, pady=5, cursor="hand2", font=(FONT, 10), bg=PAPER, fg=SOFT)
            back.bind("<ButtonRelease-1>", lambda _: studio.round_back())
        for place, candidate in enumerate(found["candidates"]):
            sound = studio.store.sound(candidate["sound"])
            chosen = candidate["sound"] == studio.state.b
            pip = ("!" if studio.unheard(sound) else "◐" if studio.ear.hearing == sound.id
                   else "●" if summary(sound.heard) else "○")
            chip = tk.Label(self.chips, text=f"{pip} {place + 1}  {sound.name}", padx=10, pady=5, cursor="hand2",
                            font=(FONT, 9, "bold" if chosen else "normal"),
                            bg=mix(SIDE["b"], DARK, .3) if chosen else TILE, fg=INK if chosen else SOFT)
            chip.bind("<ButtonRelease-1>", self.drags.click(lambda place=place: studio.step(place)))
            self.drags.source(chip, lambda event, sound=sound: ("design", sound.id), self.dragged_name)
            named(chip, lambda sound=sound: aim_against(found["aim"], None if studio.unheard(sound) else sound.heard,
                                                       studio.unheard(sound)))
            if chosen:
                aim = found["aim"]
                song = aim.get("song")
                part = f"{song.get('title')}{' · ' + song['part'] if song.get('part') else ''} · " if song else ""
                missed = misses(aim, None if studio.unheard(sound) else sound.heard)
                self.strip_note.configure(text=f"{part}{candidate['differs']}"
                                               + (f" · misses the aim on {', '.join(missed)}" if missed else ""))
        ready = bool(found.get("next"))
        # Free when they are ready; a call to the assistant of about a minute when they are not: the chip says so.
        more = tk.Label(self.chips, text="›  next three" if ready else "Three more · about a minute", padx=10, pady=5,
                        cursor="hand2", font=(FONT, 9, "bold" if ready else "normal"), bg=PAPER,
                        fg=ACCENT if ready else SOFT)
        more.bind("<ButtonRelease-1>", lambda _: studio.round_forward())
        self.flow_chips()
        self.strip_key = key

    def flow_chips(self, width=None):
        """The round's chips, as many to a line as the column has room for: with a panel open in the smallest
        window they go on to a second line rather than past its edge."""
        chips = self.chips.winfo_children()
        if chips:
            flow(self.chips, chips, width=max(200, (width or self.chips.winfo_width() or 600) - 4), gap=5)

    # ---- panels ----

    def host_frame(self):
        """A frame for something to be shown in the side panel's place (`show`). It is kept until the desk closes."""
        return tk.Frame(self.side, bg=DARK)

    # ---- the instrument, and learning it ----

    def choose_instrument(self, name):
        """The instrument picked in the chooser: for the whole app, at once. The phrase follows it when a phrase
        was recorded on that instrument; when none was, the line says whose phrase is still in use."""
        studio = self.studio
        studio.set(instrument=name)
        recorded = studio.recorded_phrases()
        current = next((item for item in recorded if studio.phrase and item["id"] == studio.phrase[0]), None)
        on_it = [item for item in recorded if item.get("instrument") == name]
        if on_it and (current is None or current.get("instrument") != name):
            self.choose_recorded(on_it[0])
            setting = on_it[0].get("setting")
            studio.say(f"Now on the {name}. The phrase is the last one recorded on it" + (f" ({setting})." if setting else "."))
        elif current is not None and current.get("instrument") not in (None, name):
            studio.say(f"Now on the {name}. No phrase is recorded on it yet: the phrase is still the "
                       f"{current['instrument']}'s.")
        else:
            studio.say(f"Now on the {name}.")

    def instrument_changed(self):
        """The instrument is another one than the desk last drew for (chosen in the chooser, or put back from the
        last session): everything that showed or used the one before follows."""
        import sys as modules

        before, self.instrument_shown = self.instrument_shown, self.studio.state.instrument
        self.chooser.refresh()
        self.record_setting = self.remembered_setting()      # the one last used on this instrument, if any
        if before is None:
            return                          # the desk is opening: nothing was on show for another instrument
        gestures = modules.modules.get("lab.learner.gestures")      # nothing to stop if it was never used
        if gestures is not None and gestures.running():
            gestures.stop()
            self.studio.say(f"Gestures off: they were listening to the {before}.")
        if self.learning_on:
            self.learning.follow()

    def toggle_learning(self):
        """The learning symbol was pressed."""
        if self.learning_on:
            self.leave_learning()
        else:
            self.learn()

    def learn(self, view=None):
        """Put the learning screen in the place of everything below the top row (at `view`, when one is named).
        The desk itself is left as it is, out of sight: the open panel, a tool on show, what plays."""
        if self.learning is None:
            from lab.learner.screen import LearningScreen

            self.learning = LearningScreen(self.learner_seat(), self.page)
        if view is not None:
            self.learning.wanted = view
        if self.learning_on:
            self.learning.follow()
            return self.learning
        self.chooser.close()
        self.body.pack_forget()
        self.names.pack_forget()
        self.learning.frame.pack(fill="both", expand=True, pady=(12, 0))
        self.learning_on = True
        self.learn_symbol.configure(image=self.symbols[True], bg=TILE, highlightbackground=ACCENT)
        self.learning.shown()
        self.draw()
        return self.learning

    def leave_learning(self):
        """Bring the desk back, as it was. A recording or a live listening of the learning screen is ended."""
        self.learning.hidden()
        self.learning.frame.pack_forget()
        self.learning_on = False
        self.learn_symbol.configure(image=self.symbols[False], bg=PAPER, highlightbackground=RULE)
        self.names.pack(side="right")
        self.body.pack(fill="both", expand=True, pady=(12, 0))
        self.draw()

    # ---- what is shown in the side panel's place ----

    def show(self, thing):
        """Show a side tool in the place of the side panel.

        `thing` has a `frame` (from `host_frame`), a `TITLE`, and `shown()` and `hidden()`, called as it comes
        and goes. One at a time: what was there is hidden and kept as it was."""
        if self.hosted is thing:
            return
        if self.hosted is not None:
            self._hide_hosted()
        self.tray.grid_remove()
        self.hosted = thing
        self.side_title.configure(text=thing.TITLE)
        thing.frame.pack(fill="both", expand=True)
        self.side.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        thing.shown()

    def back(self):
        """Back from what is on show in the side panel's place to the panel it was opened from."""
        if self.hosted is None:
            return
        self._hide_hosted()
        self.side.grid_remove()
        if self.panel:
            self.tray.grid(row=0, column=1, sticky="ns", padx=(14, 0))
            self.fill_panel()

    def _hide_hosted(self):
        thing, self.hosted = self.hosted, None
        thing.frame.pack_forget()
        thing.hidden()

    def toggle(self, name):
        if self.hosted is not None:
            self.back()
            if self.panel == name:      # the panel the hosted thing was opened from: it is on show again
                return
        from lab.tools import usage

        if self.panel == name:
            self.panel = None
            self.studio.remember(panel=None)
            self.tray.grid_remove()
            self.light_names()
            usage.record(self.studio.folder, "close", name)
            return
        if self.panel is not None:
            usage.record(self.studio.folder, "close", self.panel)
        self.panel = name
        self.studio.remember(panel=name)
        usage.record(self.studio.folder, "open", name, side=self.studio.state.side,
                     playing=self.studio.state.mode or "")
        self.tray.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        self.fill_panel()

    def light_names(self):
        """The open panel's name is lit at the top right: it says which is open, and that a click closes it."""
        for name, opener in self.openers.items():
            opener.configure(fg=INK if name == self.panel else SUBTLE,
                             font=(FONT, 10, "bold" if name == self.panel else "normal"))

    def fill_panel(self):
        self.light_names()
        self.panel_key = self.drawn_from()
        self.dragging = False
        for child in self.tray.winfo_children():
            child.destroy()
        head = tk.Frame(self.tray, bg=DARK)
        head.pack(fill="x", pady=(0, 6))
        label(head, self.panel, size=11, bold=True).pack(side="left")
        # A panel that changes or shows one sound says whose, in that side's colour.
        if self.panel in SIDED:
            side = self.studio.state.side
            label(head, f"  {side.upper()}", size=11, bold=True, color=SIDE[side]).pack(side="left")
        # A panel whose length depends on what it shows (the measurements, the takes, what the guitar puts out)
        # scrolls when it is taller than the window leaves room for. The others keep a list that takes the room
        # that is left, with their buttons under it, and scroll inside the list.
        view = None
        if self.panel in SCROLLED:
            from lab.tools.common import scrolled

            view, self.into = scrolled(self.tray)
            view.pack(fill="both", expand=True)
        else:
            self.into = self.tray
        getattr(self, f"panel_{self.panel.lower()}")()
        if view is not None:
            from lab.tools.common import wheel

            wheel(view)

    def drag_ends(self):
        """A drag in the panel is over: the panel is drawn again if what it shows changed meanwhile."""
        self.dragging = False
        if self.panel and self.drawn_from() != self.panel_key:
            self.fill_panel()

    def panel_shape(self):
        studio = self.studio
        sound = studio.sound(studio.state.side)
        if sound is None:
            label(self.tray, "Nothing to shape yet.", color=SUBTLE).pack(fill="x")
            return
        from lab.tools.common import scrolled, wheel

        values = dict(sound.knobs)
        available = studio.available()
        # Undo and hearing it without the knobs stay on show under the knobs, however many the sound has: they
        # are placed first, from the bottom, and the knobs scroll in the room that is left.
        actions = tk.Frame(self.tray, bg=DARK)
        actions.pack(side="bottom", fill="x", pady=(10, 0))
        without = studio.state.side in studio.hearing_base
        button(actions, "Hear it with the knobs again" if without else "Hear it without the knobs",
               studio.base).pack(side="left")
        if set(knob_table.PAD) & set(available):
            self.shape_pad(values, [key for key in knob_table.PAD if key in available])
        knobs_view, knob_rows = scrolled(self.tray)
        knobs_view.pack(fill="both", expand=True)
        button(knob_rows, "Add parallel space", studio.add_parallel_space).pack(anchor="w", pady=(0, 6))
        for key in available:
            if key in knob_table.PAD:
                continue
            title, low, _ = knob_table.KNOBS[key]
            row = tk.Frame(knob_rows, bg=DARK)
            row.pack(fill="x")
            label(row, title, size=9, color=SOFT).pack(side="left")
            scale = tk.Scale(row, from_=low, to=1, resolution=.05, orient="horizontal", length=230, showvalue=False,
                             bg=DARK, fg=INK, troughcolor=TILE, highlightthickness=0, borderwidth=0)
            scale.set(values.get(key, 0.))
            scale.pack(side="right")
            scale.bind("<Button-1>", lambda _: setattr(self, "dragging", True), add="+")
            scale.bind("<ButtonRelease-1>", lambda _, key=key, scale=scale: self.turned(key, scale.get()))
        wheel(knobs_view)

    def shape_pad(self, values, usable):
        """Brightness and Texture as one point on a square: dark to bright across, round to rough up. Drag the
        point; letting go turns both knobs. An axis the sound has nothing for stays at its centre, dimmed."""
        size = PAD_SIZE
        across, up = knob_table.PAD
        point = {key: float(values.get(key, 0.)) for key in knob_table.PAD}
        canvas = tk.Canvas(self.tray, width=size, height=size, bg=TILE, highlightthickness=0, cursor="crosshair")
        canvas.pack(pady=(2, 4))
        canvas.create_line(size / 2, 0, size / 2, size, fill=RULE)
        canvas.create_line(0, size / 2, size, size / 2, fill=RULE)
        for text, x, y, anchor, key in (("dark", 6, size / 2 - 4, "sw", across),
                                        ("bright", size - 6, size / 2 - 4, "se", across),
                                        ("rough", size / 2 + 6, 6, "nw", up),
                                        ("round", size / 2 + 6, size - 6, "sw", up)):
            canvas.create_text(x, y, text=text, anchor=anchor, fill=SUBTLE if key in usable else DIM, font=(FONT, 8))
        radius = 6
        x, y = pad_point(point[across], point[up], size)
        dot = canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=SIDE[self.studio.state.side],
                                 outline="")
        note = label(self.tray, size=9, color=SUBTLE)
        note.pack(fill="x", pady=(0, 8))

        def show():
            note.configure(text=f"{knob_table.KNOBS[across][0]} {point[across]:+.2f} · "
                                f"{knob_table.KNOBS[up][0]} {point[up]:+.2f}")

        def drag(event):
            self.dragging = True
            found = dict(zip(knob_table.PAD, pad_values(event.x, event.y, size)))
            point.update({key: found[key] for key in usable})
            x, y = pad_point(point[across], point[up], size)
            canvas.coords(dot, x - radius, y - radius, x + radius, y + radius)
            show()

        def done(event):
            drag(event)
            self.dragging = False
            sound = self.studio.sound(self.studio.state.side)
            if sound is not None:
                self.studio.turn({**sound.knobs, **{key: point[key] for key in usable}})

        canvas.bind("<Button-1>", drag)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", done)
        show()

    def turned(self, key, value):
        self.dragging = False
        sound = self.studio.sound(self.studio.state.side)
        if sound is not None:
            self.studio.turn({**sound.knobs, key: value})

    def panel_chain(self):
        studio = self.studio
        sound = studio.sound(studio.state.side)
        if studio.state.blind:
            label(self.into, "Hidden while you compare blind.", color=SUBTLE).pack(fill="x")
            return
        if sound is None:
            label(self.into, "Nothing here yet.", color=SUBTLE).pack(fill="x")
            return
        label(self.into, sound.name, size=12, bold=True, wrap=400).pack(fill="x")
        if sound.explanation:
            label(self.into, sound.explanation, size=9, color=SOFT, wrap=400).pack(fill="x", pady=(2, 6))
        from lab.knowledge import named_settings
        from lab.sound.sounds import settings as stated

        for r, row in enumerate(sound.chain.rows):
            if not row.blocks:
                continue
            label(self.into, f"Row {r + 1}" + (f" · split at {row.split[0] + 1}, joined at {row.split[1] + 1}"
                                                 if row.split else ""), size=8, color=DIM).pack(fill="x", pady=(4, 0))
            for column, block in row.blocks:
                line = tk.Frame(self.into, bg=DARK)
                line.pack(fill="x")
                shown = stated(block)
                text = block.name + (" · " + ", ".join(f"{key} {value:g}" if isinstance(value, float) else
                                                       f"{key} {value}" for key, value in list(shown.items())[:6])
                                     if shown else "")
                label(line, text, size=9, wrap=330).pack(side="left", fill="x", expand=True)
                names = named_settings(block.model)
                if names:
                    place = (r, column)
                    opened = place in self.expanded
                    toggle = tk.Label(line, text="named settings ▴" if opened else "named settings ▾", bg=DARK,
                                      fg=ACCENT, font=(FONT, 8), cursor="hand2")
                    toggle.pack(side="right")
                    toggle.bind("<ButtonRelease-1>", lambda _, place=place: self.expand(place))
                    if opened:
                        choices = tk.Frame(self.into, bg=DARK)
                        choices.pack(fill="x", padx=(12, 0), pady=(0, 4))
                        for name in names:
                            chip = tk.Label(choices, text=name, bg=TILE, fg=SOFT, font=(FONT, 8), padx=6, pady=2,
                                            cursor="hand2")
                            chip.pack(side="left", padx=(0, 4), pady=2)
                            chip.bind("<ButtonRelease-1>", lambda _, r=r, column=column, name=name:
                                      studio.named(r, column, name))
        from lab.hearing.measure import measured_lines

        # What was measured is behind a line to click: the tile's scales show what it amounts to.
        why = studio.unheard(sound)
        if why:
            label(self.into, f"It could not be listened to as it is set now: {why}", size=9, color=SOFT,
                  wrap=400).pack(fill="x", pady=(10, 1))
        else:
            measured = tk.Label(self.into, text="Measured ▴" if self.show_measured else "Measured ▾", bg=DARK,
                                fg=ACCENT, font=(FONT, 9), cursor="hand2")
            measured.pack(anchor="w", pady=(10, 2))
            measured.bind("<ButtonRelease-1>", lambda _: (setattr(self, "show_measured", not self.show_measured),
                                                          self.fill_panel()))
            if self.show_measured:
                for line in measured_lines(sound.heard):
                    label(self.into, line, size=9, color=SOFT, wrap=400).pack(fill="x", pady=1)
        nano = tk.Label(self.into, text="Nano Cortex sheet ▴" if self.show_nano else "Nano Cortex sheet ▾", bg=DARK,
                        fg=ACCENT, font=(FONT, 9), cursor="hand2")
        nano.pack(anchor="w", pady=(8, 2))
        nano.bind("<ButtonRelease-1>", lambda _: self.toggle_nano())
        if self.show_nano:
            from lab.gear.nano import sheet

            for line in sheet(sound.chain).lines():
                label(self.into, line, size=9, color=SOFT, wrap=400).pack(fill="x")
        for note in sound.notes[-4:]:
            label(self.into, f"You: {note['text']}", size=9, color=SUBTLE, wrap=400).pack(fill="x")

    def expand(self, place):
        self.expanded ^= {place}
        self.fill_panel()

    def toggle_nano(self):
        self.show_nano = not self.show_nano
        self.fill_panel()

    def panel_difference(self):
        """What listening heard differ between A and B; and, asked for, both played silently now with the phrase
        and compared, level aside (`lab.tools.field.contrast`)."""
        from lab.tools import field

        for line in self.studio.difference():
            label(self.into, line, size=10, wrap=400).pack(fill="x", pady=2)
        row = tk.Frame(self.into, bg=DARK)
        row.pack(fill="x", pady=(10, 4))
        button(row, "Measure A and B now", self.measure_difference).pack(side="left")
        hint(row, "Plays the phrase silently through both, as they are set now, and says how B differs from A "
                  "whatever their levels.").pack(side="left")
        found = self.contrast
        if found and found["key"] == self.contrast_key():
            for line in field.contrast_lines(found):
                label(self.into, line, size=9, wrap=400).pack(fill="x", pady=(4, 0))
            chart = tk.Canvas(self.into, bg=PAPER, width=400, height=100, highlightthickness=0)
            chart.pack(fill="x", pady=(6, 0))
            field.draw_bands(chart, found["spectrum_db"], width=400, height=100)

    def contrast_key(self):
        """What a measured contrast holds for: the two sounds as they are set, and the phrase."""
        sounds = [self.studio.sound(side) for side in ("a", "b")]
        return tuple((sound.id, json.dumps(sound.knobs, sort_keys=True)) if sound else None
                     for sound in sounds) + (self.studio.phrase_key(),)

    def measure_difference(self):
        from lab.tools.common import heard_chain, send

        studio = self.studio
        a, b = studio.sound("a"), studio.sound("b")
        if a is None or b is None or studio.phrase is None:
            studio.say("Put sounds on A and B and choose a phrase first.")
            return
        key = self.contrast_key()

        def measured(found):
            self.contrast = {**found, "key": key}
            self.refill("Difference")

        if not send(studio, "lab.tools.field.contrast", heard_chain(a), heard_chain(b), studio.phrase[1],
                    done=measured, failed=lambda error: studio.say(str(error)), renders=True,
                    working="Playing the phrase silently through A and B…"):
            studio.say(studio.held_words())

    def panel_phrase(self):
        """The phrases recorded, recording a new one with its pickup setting, and the loop: drag its edges."""
        import numpy as np

        studio = self.studio
        current = studio.phrase[0] if studio.phrase else None
        recorded = studio.recorded_phrases()
        # The newest eight, and the rest on request: an older take can always be chosen again.
        shown = recorded if self.all_takes else recorded[:8]
        if current and all(item["id"] != current for item in shown):
            shown += [item for item in recorded if item["id"] == current]
        for item in shown:
            chosen = item["id"] == current
            row = tk.Label(self.into, text=f"{item.get('instrument', '')} · {item.get('setting', '')} · "
                                           f"{str(item.get('recorded', ''))[5:16].replace('T', ' ')}",
                           bg=TILE if chosen else DARK, fg=INK if chosen else SOFT, font=(FONT, 9), anchor="w",
                           padx=6, pady=2, cursor="hand2")
            row.pack(fill="x")
            row.bind("<ButtonRelease-1>", lambda _, item=item: (self.choose_recorded(item), self.fill_panel()))
        if len(recorded) > 8:
            more = tk.Label(self.into, text="The newest eight ▴" if self.all_takes
                            else f"All {len(recorded)} takes ▾", bg=DARK, fg=ACCENT, font=(FONT, 9),
                            cursor="hand2", anchor="w")
            more.pack(fill="x", pady=(2, 0))
            more.bind("<ButtonRelease-1>", lambda _: (setattr(self, "all_takes", not self.all_takes),
                                                       self.fill_panel()))
        self.recording_controls()
        whole = studio.phrase_whole
        if whole is None:
            return
        audio, start, end = whole
        width, height = 400, 120
        canvas = tk.Canvas(self.into, width=width, height=height, bg=TILE, highlightthickness=0, cursor="sb_h_double_arrow")
        canvas.pack(fill="x", pady=(10, 0))
        columns = np.array_split(np.abs(audio[:, 0]), width)
        peak = max(float(np.abs(audio).max()), 1e-6)
        for x, part in enumerate(columns):
            value = float(part.max()) / peak if len(part) else 0.
            canvas.create_line(x, height / 2 - value * height / 2, x, height / 2 + value * height / 2, fill=SOFT)
        shade = canvas.create_rectangle(0, 0, 0, height, fill=ACCENT, stipple="gray25", width=0)
        seconds = len(audio) / RATE
        under = tk.Frame(self.into, bg=DARK)
        under.pack(fill="x", pady=(4, 0))
        note = label(under, size=9, color=SUBTLE)
        note.pack(side="left")
        hint(under, "Drag either edge of the shaded part to change what loops. The recording itself is never "
                    "changed.").pack(side="left")
        edges = {"now": (start, end)}

        def draw():
            first, last = edges["now"]
            canvas.coords(shade, first / len(audio) * width, 0, last / len(audio) * width, height)
            note.configure(text=f"Loop {first / RATE:.1f}–{last / RATE:.1f} s of {seconds:.1f} s")

        def drag(event):
            self.dragging = True
            at = int(min(max(event.x, 0), width) / width * len(audio))
            edges["now"] = loop_edges(edges["now"], at, min(SHORTEST_LOOP, len(audio)))
            draw()

        def done(_):
            if edges["now"] != (start, end) and studio.phrase:
                self.dragging = False
                studio.use_phrase(studio.phrase[0], audio, *edges["now"])
            else:
                self.drag_ends()

        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", done)
        draw()

    def panel_sounds(self):
        """What is on the unit: presets to work on, and the player's IRs and captures to try in B."""
        kinds = tk.Frame(self.tray, bg=DARK)
        kinds.pack(fill="x", pady=(0, 6))
        for kind in ("Presets", "IRs", "Captures"):
            chosen = kind == self.sounds_kind
            tab = tk.Label(kinds, text=kind, padx=8, pady=3, cursor="hand2", font=(FONT, 9, "bold" if chosen else "normal"),
                           bg=TILE if chosen else DARK, fg=INK if chosen else SUBTLE)
            tab.pack(side="left", padx=(0, 4))
            tab.bind("<ButtonRelease-1>", lambda _, kind=kind: (setattr(self, "sounds_kind", kind), self.fill_panel()))
        if self.sounds_kind != "Presets":
            self.panel_assets("ir" if self.sounds_kind == "IRs" else "capture")
            return
        from lab.qc.library import cached, place
        from lab.tools import library
        from lab.tools.common import SCENE_LETTERS

        entries = cached(self.studio.folder)
        top = tk.Frame(self.tray, bg=DARK)
        top.pack(fill="x", pady=(0, 6))
        button(top, "Read the unit again", self.read_directory).pack(side="right")
        if not entries:
            label(top, "Not read yet." if entries is None else "No presets of yours on the unit.",
                  color=SUBTLE).pack(side="left")
            return
        search = tk.Entry(top, font=(FONT, 10), bg=TILE, fg=INK, relief="flat", insertbackground=INK)
        search.insert(0, self.sounds_query)
        search.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 8))
        # What is under the list is placed first, from the bottom, so that the list takes the room that is left
        # and nothing under it is pushed out of the panel.
        foot = tk.Frame(self.tray, bg=DARK)
        foot.pack(side="bottom", fill="x")
        box = tk.Listbox(self.tray, bg=DARK, fg=INK, selectbackground=TILE, relief="flat", highlightthickness=0,
                         font=(FONT, 9), height=6, exportselection=False)
        box.pack(fill="both", expand=True)
        rows = []

        def fill(*_):
            # Every word typed must be in the name, the setlist or the slot as the unit shows it ("12B").
            self.sounds_query = search.get()
            rows[:] = [(item["slot"], item["name"], item["setlist"])
                       for item in library.filtered(entries, self.sounds_query)]
            box.delete(0, "end")
            for slot, name, setlist in rows:
                box.insert("end", f"{name}  ·  {setlist} {place(slot['position'])}")

        def chosen():
            picked = box.curselection()
            if not picked:
                self.studio.say("Choose a preset in the list first.")
            return rows[picked[0]] if picked else None

        fill()
        search.bind("<KeyRelease>", fill)       # the list follows what is typed
        box.bind("<Double-Button-1>", lambda _: self.work_on(box, rows))      # the same as the button below
        button(foot, "Work on this", lambda: self.work_on(box, rows)).pack(fill="x", pady=(6, 0))
        more = tk.Label(foot, text="More with this preset " + ("▴" if self.preset_more else "▾"), bg=DARK,
                        fg=ACCENT, font=(FONT, 9), cursor="hand2", anchor="w")
        more.pack(fill="x", pady=(6, 0))
        more.bind("<ButtonRelease-1>", lambda _: (setattr(self, "preset_more", not self.preset_more),
                                                   self.fill_panel()))
        if not self.preset_more:
            return
        scenes = tk.Frame(foot, bg=DARK)
        scenes.pack(fill="x", pady=(2, 2))
        for index, scene in enumerate(("Default scene", *SCENE_LETTERS)):
            lit = scene == self.reference_scene
            chip = tk.Label(scenes, text=scene, bg=TILE if lit else DARK, fg=INK if lit else SOFT, font=(FONT, 8),
                            padx=6, pady=2, cursor="hand2")
            chip.grid(row=0, column=index, padx=(0, 2))
            chip.bind("<ButtonRelease-1>", lambda _, scene=scene: (setattr(self, "reference_scene", scene),
                                                                   self.fill_panel()))
        actions = tk.Frame(foot, bg=DARK)
        actions.pack(fill="x", pady=(2, 0))
        button(actions, "Use as reference", lambda: self.reference_preset(chosen())).pack(side="left")
        hint(actions, "Plays the phrase silently through the preset, in the scene chosen above, and keeps what came "
                      "back for Tone search to match against.").pack(side="left")
        button(actions, "Leave it on the unit", lambda: self.leave_preset(chosen())).pack(side="left", padx=(6, 0))
        hint(actions, "Selects the preset on the unit. It stays selected when the app closes.").pack(side="left")

    def reference_preset(self, row):
        """Play the phrase silently through a saved preset and keep the return as a reference for Tone search."""
        from lab.tools import library
        from lab.tools.common import SCENE_LETTERS, send

        studio = self.studio
        if row is None:
            return
        if studio.phrase is None:
            studio.say("Record or choose a phrase first.")
            return
        slot, name, setlist = row
        scene = None if self.reference_scene == "Default scene" else SCENE_LETTERS.index(self.reference_scene)
        phrase_name = studio.phrase[0]

        def captured(found):
            library.keep_reference(Path(studio.folder), {"slot": slot, "setlist": setlist}, scene, found, phrase_name)
            studio.say(f"Reference ready · {found['name']}" + (f" · scene {self.reference_scene}" if scene is not None
                                                               else "") + ". Tone search can match against it.")

        if not send(studio, "lab.tools.library.capture", slot, studio.phrase[1], scene, done=captured,
                    failed=lambda error: studio.say(str(error)), renders=True,
                    working=f"Playing the phrase silently through {name}…"):
            studio.say(studio.held_words())

    def leave_preset(self, row):
        """Select a saved preset on the unit and make it the one given back when the app closes. A preset being
        edited on the unit is not recalled over (`library.leave`)."""
        from lab.tools.common import send

        studio = self.studio
        if row is None:
            return
        slot, name, _ = row
        if not send(studio, "lab.tools.library.leave", slot, renders=True, working=f"Selecting {name}…",
                    done=lambda record: studio.say(f"{record['name']} is selected and stays selected when the app "
                                                   "closes."),
                    failed=lambda error: studio.say(str(error))):
            studio.say(studio.held_words())

    def panel_assets(self, kind):
        from lab.qc.assets import cached

        found = [item for item in cached(self.studio.folder) or [] if item.kind == kind]
        top = tk.Frame(self.tray, bg=DARK)
        top.pack(fill="x", pady=(0, 6))
        button(top, "Read the unit again", lambda: self.studio.read_assets(lambda _: self.refill("Sounds"))).pack(
            side="right")
        if not found:
            label(top, "Not read yet.", color=SUBTLE).pack(side="left")
            return
        search = tk.Entry(top, font=(FONT, 10), bg=TILE, fg=INK, relief="flat", insertbackground=INK)
        search.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 8))
        mine = tk.BooleanVar(value=True)
        box = tk.Listbox(self.tray, bg=DARK, fg=INK, selectbackground=TILE, relief="flat", highlightthickness=0,
                         font=(FONT, 9), height=22)
        rows = []

        def fill(*_):
            wanted = search.get().strip().casefold()
            rows[:] = [item for item in found if wanted in item.name.casefold()
                       and (item.source == "user" or not mine.get())]
            box.delete(0, "end")
            for item in rows[:SHOWN_AT_MOST]:
                version = f"  · V{item.capture_version}" if item.capture_version else ""
                box.insert("end", item.name + version)
            # A list cut short says so, and how to reach the rest.
            cut.configure(text=f"The first {SHOWN_AT_MOST} of {len(rows)}: type to narrow them."
                          if len(rows) > SHOWN_AT_MOST else "")

        tk.Checkbutton(self.tray, text="Only mine", variable=mine, command=fill, bg=DARK, fg=SOFT,
                       selectcolor=TILE, activebackground=DARK, font=(FONT, 9)).pack(anchor="w")
        cut = label(self.tray, size=9, color=SUBTLE)
        cut.pack(fill="x")
        box.pack(fill="both", expand=True)
        fill()
        search.bind("<KeyRelease>", fill)

        def chosen():
            picked = box.curselection()
            if not picked:
                self.studio.say("Choose one in the list first.")
            return rows[picked[0]] if picked else None

        def use(**options):
            asset = chosen()
            if asset is not None:
                self.studio.use_asset(asset, **options)

        actions = tk.Frame(self.tray, bg=DARK)
        actions.pack(fill="x", pady=(6, 0))
        if kind == "ir":
            side = self.studio.state.side.upper()
            button(actions, f"Put in {side}'s cab", use).pack(side="left")
            # An IR dragged onto a tile goes into that sound's cab.
            self.drags.source(box, lambda event: ("asset", rows[box.nearest(event.y)]) if rows else None,
                              self.dragged_name)
        else:
            versions = tk.Frame(self.tray, bg=DARK)
            versions.pack(fill="x", pady=(6, 0))
            label(versions, "If the unit does not say, the capture is", size=9, color=SUBTLE).pack(side="left")
            for value in (1, 2):
                chosen_version = value == self.capture_version
                chip = tk.Label(versions, text=f"V{value}", bg=TILE if chosen_version else DARK,
                                fg=INK if chosen_version else SOFT, font=(FONT, 9), padx=6, cursor="hand2")
                chip.pack(side="left", padx=(6, 0))
                chip.bind("<ButtonRelease-1>", lambda _, value=value: (setattr(self, "capture_version", value),
                                                                       self.fill_panel()))
            side = self.studio.state.side.upper()
            button(actions, f"Instead of {side}'s amp", lambda: self.try_capture(chosen(), False)).pack(side="left")
            button(actions, "Instead of amp and cab", lambda: self.try_capture(chosen(), True)).pack(
                side="left", padx=6)

    def try_capture(self, asset, has_cab):
        if asset is None:
            return
        self.studio.use_asset(asset, has_cab=has_cab, version=asset.capture_version or self.capture_version)

    def refill(self, panel):
        """Draw a panel again, if it is the one open."""
        if self.panel == panel:
            self.fill_panel()

    def read_directory(self):
        self.studio.read_directory(lambda entries: self.refill("Sounds"))

    def work_on(self, box, rows):
        chosen = box.curselection()
        if not chosen:
            self.studio.say("Choose a preset in the list first.")
            return
        slot, name, _ = rows[chosen[0]]
        self.studio.work_on(slot, name)

    def panel_history(self):
        """Songs and their parts, collected from requests for songs; what the player's choices say; then every
        sound kept, newest first, to put on A or B."""
        studio = self.studio
        for song in studio.store.songs():
            parts = []
            for part in song.get("parts", []):
                try:
                    parts.append(f"{part['name']}: {studio.store.sound(part['sound']).name}")
                except Exception:  # noqa: BLE001  a part whose sound is gone is not listed
                    continue
            if parts:
                label(self.tray, f"{song['name']} · " + ", ".join(parts), size=9, wrap=400).pack(fill="x", pady=1)
        for line in studio.taste_lines():
            label(self.tray, line, size=9, color=SOFT, wrap=400).pack(fill="x", pady=1)
        rows = history_rows(studio.folder)
        box = tk.Listbox(self.tray, bg=DARK, fg=INK, selectbackground=TILE, relief="flat", highlightthickness=0,
                         font=(FONT, 9), height=24)
        box.pack(fill="both", expand=True)
        for _, name, kind, saved in rows:
            box.insert("end", f"{name}  ·  {kind}" + ("  ★" if saved else ""))
        # A row dragged onto a tile goes on that side, as the buttons under the list do.
        self.drags.source(box, lambda event: ("sound", rows[box.nearest(event.y)][0]) if rows else None,
                          self.dragged_name)

        def put(side):
            chosen = box.curselection()
            if not chosen:
                studio.say("Choose a sound in the list first.")
                return
            # As it is on disk now: what was listed may have been measured or marked since.
            studio.put(side, studio.store.sound(rows[chosen[0]][0]))

        row = tk.Frame(self.tray, bg=DARK)
        row.pack(fill="x", pady=(6, 0))
        for side in ("a", "b"):
            button(row, f"Put on {side.upper()}", lambda side=side: put(side)).pack(side="left", padx=(0, 6))

    # ---- actions ----

    def toggle_play(self, mode):
        """Guitar or Phrase was pressed: start it, or stop it when it is what plays. While something else has the
        audio input (`Studio.audio_held_by`), the studio says who instead of starting."""
        self.play_when_free = None
        if self.studio.state.mode == mode:
            self.studio.stop()
            return
        if mode == "phrase":
            # The phrase loop and gesture listening would both want the audio driver.
            from lab.learner import gestures

            if gestures.running():
                gestures.stop()
                self.studio.say("Gestures off while the phrase plays.")
            if self.studio.audio_held_by == "gestures":
                self.play_when_free = mode      # it lets go within a tick or two: the phrase starts then
                return
        self.studio.play(mode)

    def ask(self):
        """Send what was typed. The focus stays in the line, for the next request."""
        text = self.request.get()
        self.request.delete(0, "end")
        self.studio.ask(text)
        self.show_placeholder()

    def show_placeholder(self):
        """The words in the ask line while nothing is typed in it."""
        empty = not self.request.get()
        if empty and not self.placeholder.winfo_manager():
            self.placeholder.place(x=13, rely=.5, anchor="w")
        elif not empty and self.placeholder.winfo_manager():
            self.placeholder.place_forget()

    def save(self):
        """Show the name to save under, in place, filled in with the sound's own."""
        sound = self.studio.sound(self.studio.state.side)
        if sound is None:
            self.studio.say(f"Nothing is on {self.studio.state.side.upper()} to save.")
            return
        # The name is for this sound with these knobs, whatever is selected by the time it is confirmed.
        self.save_target = (sound.id, dict(sound.knobs))
        self.save_name.delete(0, "end")
        if not self.studio.state.blind:     # filled in with the name only when it is not hidden
            self.save_name.insert(0, sound.name[:24])
        self.save_row.pack(side="bottom", fill="x", pady=(8, 0), before=self.save_row_after)
        self.save_name.focus_set()

    def saved(self):
        name = self.save_name.get().strip()[:24]
        if not name:
            self.studio.say("Type a name to save under.")
            return
        self.save_row.pack_forget()
        target, self.save_target = self.save_target, None
        if target:
            self.studio.save(name, sound_id=target[0], knob_values=target[1])

    def recording_controls(self):
        """Record a phrase: the pickup setting it is played on, then Record (eight seconds, from the guitar)."""
        instrument = self.studio.store.instrument(self.studio.state.instrument) or {}
        settings = [f"{position} · {wiring}".rstrip(" ·") for position in instrument.get("settings") or ["Pickup"]
                    for wiring in (instrument.get("wiring") or [""])]
        label(self.into, f"Record a phrase on the {self.studio.state.instrument}", size=10, bold=True).pack(
            fill="x", pady=(12, 0))
        label(self.into, "Pickup setting", size=9, color=SUBTLE).pack(fill="x", pady=(4, 2))
        grid = tk.Frame(self.into, bg=DARK)
        grid.pack(fill="x")
        chips = []
        for setting in settings:
            chosen = setting == self.record_setting
            chip = tk.Label(grid, text=setting, bg=TILE if chosen else DARK, fg=INK if chosen else SOFT, font=(FONT, 8),
                            padx=6, pady=2, cursor="hand2")
            chip.bind("<ButtonRelease-1>", lambda _, setting=setting: self.choose_setting(setting))
            chips.append(chip)
        flow(grid, chips, width=400)        # as many to a row as fit the panel, however long the names
        # What is played decides what a take measures as much as the pickup does, so pickup settings are compared
        # only on takes marked as the same gesture (`lab.hearing.profile`). Choosing one is optional.
        from lab.hearing.profile import GESTURES

        played = tk.Frame(self.into, bg=DARK)
        played.pack(fill="x", pady=(6, 2))
        label(played, "What is played", size=9, color=SUBTLE).pack(side="left")
        hint(played, "Pickup settings are compared only on takes of the same thing. To compare them, play the same "
                     "thing on each setting and mark it here; otherwise leave it.").pack(side="left")
        for gesture in GESTURES:
            chosen = gesture == self.record_gesture
            chip = tk.Label(self.into, text=gesture, bg=TILE if chosen else DARK, fg=INK if chosen else SOFT,
                            font=(FONT, 8), padx=6, pady=2, cursor="hand2", anchor="w")
            chip.pack(fill="x", pady=1)
            chip.bind("<ButtonRelease-1>", lambda _, gesture=gesture: self.choose_gesture(gesture))
        go = button(self.into, f"Record {RECORD_SECONDS:g} s" if self.record_setting else "Choose the setting first",
                    lambda: self.record_setting and self.studio.record(self.record_setting, RECORD_SECONDS,
                                                                       gesture=self.record_gesture))
        go.pack(anchor="w", pady=(4, 0))

    def choose_setting(self, setting):
        """The pickup setting the next take is played on: kept for this instrument, for the dot by the phrase."""
        self.record_setting = setting
        settings = dict(self.studio.desk_session.get("settings") or {})
        settings[self.studio.state.instrument] = setting
        self.studio.remember(settings=settings)
        self.fill_panel()

    def choose_gesture(self, gesture):
        """Mark the next take as this gesture, or, pressed again, as none."""
        self.record_gesture = None if gesture == self.record_gesture else gesture
        self.fill_panel()

    def choose_recorded(self, item, start=None, end=None):
        """Make a recorded take the phrase: the part of it kept from before, or its loudest few seconds."""
        whole = read_wave(self.studio.folder / "phrases" / item["id"] / "dry.wav")[:, :1]
        self.studio.use_phrase(item["id"], whole, start, end)

    def panel_guitar(self):
        """Gesture listening, what the instrument puts out, and recording a take. (Learning the guitar is a screen
        of its own, behind the learning symbol at the top left.)"""
        from lab.learner import gestures

        instrument = self.studio.state.instrument
        row = tk.Frame(self.into, bg=DARK)
        row.pack(fill="x", pady=(0, 8))
        state = gestures.state()
        self.gestures_shown = _gestures_shown(state)
        button(row, f"Gestures off · armed {state['seconds_left']:.0f} s" if state["armed"]
               else "Gestures off" if state["running"] else "Gestures on", self.toggle_gestures).pack(side="left")
        hint(row, f"A return gesture taught on a guitar control (Teach a control, on the learning screen) turns its "
                  f"Shape knob on the sound on show. Gestures stay on for {gestures.ARM_SECONDS:.0f} s, and "
                  f"{gestures.ARM_SECONDS:.0f} s more after each gesture.").pack(side="left")
        label(self.into, f"What the {instrument} puts out", size=10, bold=True).pack(fill="x")
        # Worked out away from the window (reading the takes takes a second or more); the panel is drawn again
        # when it arrives.
        for line in self.studio.guitar_lines():
            label(self.into, line, size=9, wrap=400).pack(fill="x", pady=2)
        self.recording_controls()

    def panel_more(self):
        """The side tools, and measuring the USB round trip."""
        from lab.tools.menu import TOOLS, opener

        grid = tk.Frame(self.into, bg=DARK)
        grid.pack(fill="x", pady=(2, 10))
        grid.columnconfigure((0, 1), weight=1, uniform="tools")
        for index, (title, name) in enumerate(TOOLS):
            chip = tk.Label(grid, text=title, bg=TILE, fg=INK, font=(FONT, 9), padx=8, pady=4, cursor="hand2",
                            anchor="w")
            chip.grid(row=index // 2, column=index % 2, sticky="we", padx=(0, 4), pady=2)
            chip.bind("<ButtonRelease-1>", lambda _, name=name: opener(name)(self))
        button(self.into, "Measure the USB round trip", self.studio.measure_round_trip).pack(anchor="w")

    def learner_seat(self):
        """The Guitar Learner's seat at this desk, made when something of the learner is first opened. With a
        simulated unit it opens no audio driver: there is no unit behind the real one."""
        return self.seat or LearnerSeat(self, real_audio=not self.studio.host.simulated)

    def toggle_gestures(self):
        from lab.learner import gestures

        if gestures.running():
            gestures.stop()
            self.studio.say("Gestures off.")
        else:
            try:                                        # refused, with the reason, on a simulated unit
                gestures.start(self.learner_seat())
                self.studio.say(f"Gestures on for {gestures.ARM_SECONDS:.0f} s: play, and the taught control turns "
                                "its knob on the sound on show.")
            except ValueError as error:
                self.studio.say(f"Gestures: {error}")
        self.refill("Guitar")

    def follow_gestures(self):
        """While the Guitar panel is open, draw it again when what it shows of gesture listening has changed:
        about once a second while it is armed, since it shows the seconds left."""
        if self.panel != "Guitar" or self.dragging or self.hosted is not None:
            return
        from lab.learner import gestures

        if _gestures_shown(gestures.state()) != self.gestures_shown:
            self.fill_panel()

    def clear_listening(self):
        """Take the Levels tool's offset or mute off. Through the tool when it is open, so that it shows the
        same."""
        levels = self.tools.get("Levels")
        if levels is not None and levels.alive():
            levels.change(None)
            self.studio.say("Back at the desk's own level.")
        else:
            self.studio.clear_listening()

    def failed(self, kind, error, trace):
        """A fault in a click: logged with its trace, and said on the line."""
        self.studio.log_error(error, "".join(traceback.format_exception(kind, error, trace)))
        self.studio.fault(f"Something went wrong: {error}. The details are in errors.log in the data folder.")

    def tick(self):
        try:
            self.studio.poll()
            if self.seat is not None:
                self.seat.tick()
            if self.play_when_free and not self.studio.audio_held_by:
                mode, self.play_when_free = self.play_when_free, None
                self.studio.play(mode)
            self.follow_gestures()
            self.show_working()
            self.show_placeholder()
            self.come_forward()
        finally:
            self.clock = self.root.after(80, self.tick)

    def come_forward(self):
        """A second launch knocked: say that a desk is here (so that it leaves), and come forward."""
        import socket

        if self.door is None:
            return
        try:
            knock, _ = self.door.accept()
        except (BlockingIOError, OSError):
            return
        try:
            knock.settimeout(.3)
            knock.sendall(HERE)
            knock.shutdown(socket.SHUT_WR)
            while knock.recv(64):           # what the launch sent is read, so that closing does not cut the answer off
                pass
        except OSError:
            pass
        knock.close()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def close(self):
        if self.closing:
            return
        self.closing = True
        if self.seat is not None:
            self.seat.leaving = True        # which ends its recording or listening and gives the input back
        self.studio.begin("close", "Giving the Quad Cortex back…")
        self.root.update()
        try:
            self.studio.close()
        finally:
            # The door closes with the window: a process that outlives it must not look like an open desk.
            if self.door is not None:
                self.door.close()
                self.door = None
            self.root.destroy()


def main(arguments):
    """Open the desk. The first thing done is the single-instance check: the desk's port is bound before anything
    else is started (the first run's checks, the engine, the window). When another desk has it, that desk is
    asked to come forward and this launch leaves."""
    from lab import setup
    from lab.qc.host import Host

    simulated = "--simulated" in arguments
    door = None
    if not simulated:
        door, another = claim_door()
        if another:
            return
    folder = home() / ("simulated" if simulated else "")
    folder.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    notice = setup.first_run(folder) if not simulated else ""
    host = Host(folder, simulated=simulated)
    studio = Studio(host, folder)
    studio.notice = notice
    Desk(root, studio, door=door)
    root.mainloop()


PORT = 47231            # on this computer only: a second desk asks the first to come forward, and leaves
HERE = b"Tone Lab desk"  # what a desk answers a knock with: anything else on that port is not a desk
WAIT_FOR_CLOSING = 20.  # seconds a new launch waits for a desk that holds the door without answering


def bind_door():
    """The desk's port, bound and listening, or None when something has it already."""
    import socket

    door = socket.socket()
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            door.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)     # nobody binds beside it
        door.bind(("127.0.0.1", PORT))
        door.listen(2)
        door.setblocking(False)
    except OSError:
        door.close()
        return None
    return door


def knock():
    """Ask whoever has the desk's port to come forward. True when a desk answered: only a desk's own answer
    (`HERE`) counts, so another program on that port, or a desk that no longer answers, is not taken for one."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=.5) as other:
            other.settimeout(2.)            # the knock is the connection itself: a desk answers it and comes forward
            return other.recv(len(HERE)) == HERE
    except OSError:
        return False


def claim_door():
    """The single-instance check. Returns (door, another): the port bound for this desk, or None; and whether
    another desk is open (it was asked to come forward: two desks would fight over the unit).

    A desk that is closing still holds the port for a while without answering (it is giving the unit back): it
    is waited for rather than opened beside. Something else that keeps the port does not keep the desk from
    opening: after the wait it opens without a door."""
    import time

    deadline = time.monotonic() + WAIT_FOR_CLOSING
    while True:
        door = bind_door()
        if door is not None:
            return door, False
        if knock():
            return None, True
        if time.monotonic() > deadline:
            return None, False
        time.sleep(.5)


def _gestures_shown(state):
    """What the Guitar panel shows of gesture listening (`gestures.state()`): it is drawn again when this changes."""
    return state["running"], state["armed"], round(state["seconds_left"])


if __name__ == "__main__":
    main(sys.argv[1:])
