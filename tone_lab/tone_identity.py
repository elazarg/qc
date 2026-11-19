"""Keep a selected tone independent of the performance used to audition it."""

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re

from . import device, draft_render, feedback, listening, session
from .preset_library import SavedPreset


def _scene(value):
    if isinstance(value, str) and len(value) == 1 and value in device.SCENES:
        return device.SCENES.index(value)
    if type(value) is int and 0 <= value < len(device.SCENES):
        return value
    return None


def _preset(value):
    if not isinstance(value, dict):
        return None
    address = value.get("position")
    if not isinstance(address, dict) or address.get("is_factory"):
        return None
    key, index = address.get("folder_key"), address.get("position")
    name, setlist = value.get("name"), value.get("setlist")
    if (not all(isinstance(item, str) and item.strip() for item in (key, name, setlist))
            or type(index) is not int or not 0 <= index < 256):
        return None
    return SavedPreset(name, setlist, key, index)


def _preset_record(choice):
    return {"name": choice.name, "setlist": choice.setlist, "position": choice.position}


def _draft_preset(draft_id):
    # Draft IDs are filenames. Older sessions have only a display label for My Presets.
    if not isinstance(draft_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", draft_id):
        return None, None, None, ()
    receipt_path = session.SESSIONS / "drafts" / f"{draft_id}.kept.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, None, ()
    if not isinstance(receipt, dict):
        return None, None, None, ()
    choice = _preset(receipt.get("preset"))
    if choice:
        fingerprint = receipt.get("fingerprint") or receipt["preset"].get("fingerprint")
        try:
            values = _values(receipt.get("knob_values"))
        except (AttributeError, ValueError):
            return None, None, None, ()
        return choice, _scene(receipt.get("scene")), fingerprint, values
    label = receipt.get("label")
    match = re.fullmatch(r"(.+) · My Presets (\d{1,2})([A-H])", label) if isinstance(label, str) else None
    if match and 1 <= int(match[2]) <= 32:
        index = (int(match[2]) - 1) * len(device.SCENES) + device.SCENES.index(match[3])
        return SavedPreset(match[1], "My Presets", "/media/p4/Presets/My Presets", index), None, None, ()
    return None, None, None, ()


def _values(values):
    return tuple(sorted(draft_render.clean_values(values).items()))


def _recorded_take(sound):
    return sound.take_id and sound.path.name == "wet.wav" and sound.path.parent.name == sound.take_id


def _manifest(sound):
    if sound.folder is not None:
        try:
            value = session.load_session(sound.folder)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            pass
    return {}


def _is_original(sound, manifest):
    rows = manifest.get("candidates")
    return isinstance(rows, list) and any(
        isinstance(row, dict) and row.get("file") == sound.path.name and row.get("audition_role") == "original"
        for row in rows
    )


@dataclass(frozen=True)
class Tone:
    """An original, a draft recipe, a recallable preset, or a captured sound."""

    kind: str
    name: str
    draft_id: str | None = None
    preset: SavedPreset | None = None
    scene: int | None = None
    capture: listening.Sound | None = None
    fingerprint: str | None = None
    values: tuple[tuple[str, float], ...] = ()

    @classmethod
    def original(cls):
        return cls("original", "Original recording")

    @classmethod
    def from_draft(cls, draft):
        tone = cls("draft", draft["name"], draft_id=draft["id"])
        choice, scene, fingerprint, values = _draft_preset(tone.draft_id)
        return tone.with_preset(choice, scene, fingerprint=fingerprint, values=dict(values)) if choice else tone

    @classmethod
    def from_preset(cls, choice, scene=None, *, fingerprint=None):
        return cls("preset", choice.name, preset=choice, scene=_scene(scene), fingerprint=fingerprint)

    @classmethod
    def from_sound(cls, sound):
        if sound is None:
            return None
        if _recorded_take(sound):
            return cls.original()
        manifest = _manifest(sound)
        source = manifest.get("qc_reference")
        choice = _preset(source)
        if choice:
            return cls.from_preset(choice, source.get("scene"), fingerprint=source.get("fingerprint")).with_capture(sound)
        if sound.draft_id:
            return cls.from_draft({"id": sound.draft_id, "name": sound.name}).with_capture(sound)
        if _is_original(sound, manifest):
            return cls.original()
        return cls("capture", sound.name, capture=sound)

    def with_preset(self, choice, scene=None, *, fingerprint=None, values=None):
        """Promote a draft to the saved hardware address without losing its prior audition."""
        scene = _scene(scene)
        capture = self.capture
        if capture and not capture.draft_id and (self.preset, self.scene, self.fingerprint) != (choice, scene, fingerprint):
            capture = None
        return replace(self, kind="preset", name=choice.name, preset=choice, scene=scene,
                       fingerprint=fingerprint, capture=capture, values=self.values if values is None else _values(values))

    def with_values(self, values):
        """Identify the exact variant created by the current tone directions."""
        return replace(self, values=_values(values))

    def with_capture(self, sound):
        """Attach an audition without making a different take the active phrase."""
        manifest = _manifest(sound) if sound else {}
        if self.preset and sound:
            source = manifest.get("qc_reference")
            if _preset(source) == self.preset:
                return replace(self, scene=_scene(source.get("scene")), capture=sound,
                               fingerprint=source.get("fingerprint") or self.fingerprint)
        if self.kind == "draft" and sound and sound.draft_id == self.draft_id:
            return replace(self, capture=sound, values=_values(manifest.get("knob_values")))
        return replace(self, capture=sound)

    def to_record(self):
        return {"kind": self.kind, "name": self.name, "draft_id": self.draft_id,
                "preset": _preset_record(self.preset) if self.preset else None,
                "scene": self.scene, "capture": self.capture.to_record() if self.capture else None,
                "fingerprint": self.fingerprint, "values": dict(self.values)}

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, dict):
            return None
        kind, name = record.get("kind"), record.get("name")
        if kind == "original":
            return cls.original()
        if kind not in ("draft", "preset", "capture") or not isinstance(name, str) or not name.strip():
            return None
        draft_id, choice = record.get("draft_id"), _preset(record.get("preset"))
        if (kind == "draft" and not isinstance(draft_id, str)) or (kind == "preset" and choice is None):
            return None
        sound = listening.Sound.from_record(record.get("capture"))
        try:
            values = _values(record.get("values"))
        except (AttributeError, ValueError):
            return None
        if kind == "draft":
            # A draft may have been saved from another route since this desk state was written.
            tone = cls.from_draft({"id": draft_id, "name": name}).with_capture(sound)
            # An unsaved draft has no receipt to say which scene was chosen, so the record does.
            return tone if tone.preset else replace(tone, values=values, scene=_scene(record.get("scene")))
        return cls(kind, name, draft_id, choice, _scene(record.get("scene")), sound, record.get("fingerprint"), values)

    def asset(self, take):
        """Resolve a same-phrase audition, or keep the tone selected without a render."""
        if take is None:
            return None
        if self.kind == "original":
            return take.original
        draft_eligible = self.draft_id is not None
        if draft_eligible and self.preset and self.fingerprint:
            receipt = _draft_preset(self.draft_id)
            draft_eligible = receipt[2:] == (self.fingerprint, self.values)
        if self.capture and self.capture.paired_with(take.original) and self.capture.path.is_file():
            if not self.capture.draft_id or (draft_eligible and self.capture.draft_id == self.draft_id
                                             and self._draft_render_matches(_manifest(self.capture))):
                return self.capture
        if self.kind == "preset":
            for sound, source in listening.reference_history():
                choice = _preset(source)
                if (choice == self.preset and self.scene is not None and _scene(source.get("scene")) == self.scene
                        and (not self.fingerprint or source.get("fingerprint") == self.fingerprint)
                        and sound.paired_with(take.original)):
                    return sound
        if draft_eligible:
            for folder, record in feedback.comparisons(take.folder.name):
                if record.get("draft_id") != self.draft_id:
                    continue
                try:
                    if self._draft_render_matches(record) and (pair := listening.comparison(folder, take)):
                        return pair[1]
                except (OSError, AttributeError, ValueError):
                    continue
        return None

    def _draft_render_matches(self, manifest):
        rendered_scene = _scene(manifest.get("draft_scene"))
        if rendered_scene is None:
            rendered_scene = 0
        return (self.scene is None or rendered_scene == self.scene) and _values(manifest.get("knob_values")) == self.values
