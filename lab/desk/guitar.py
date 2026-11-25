"""The takes recorded on the player's instruments, and what an instrument puts out at each pickup setting
(`lab.hearing.profile`), worked out from them.

Reading the takes takes a second or more, so a profile is worked out on a worker and kept until the takes
change: `lines` for the Guitar panel (which says "working it out" meanwhile, and `version` changes when it
arrives), `profile` on the assistant's thread for the designer.
"""

import json
import threading


class Guitar:
    def __init__(self, studio):
        self.studio = studio
        self.profiles = {}                # instrument -> (the takes it was made from, (profile, lines))
        self.lock = threading.Lock()
        self.profiling = None             # the takes a profile is being worked out from
        self.failed = None                # (those takes, why)
        self.version = 0                  # bumped when a profile arrives or a take is added, for the window

    # ---- the takes ----

    def recorded(self):
        studio = self.studio
        found = []
        for folder in (studio.folder / "phrases").glob("*"):
            try:
                found.append((folder.stat().st_mtime_ns, json.loads((folder / "phrase.json").read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
        # Newest first, by when it was played; the file time only breaks ties.
        found.sort(key=lambda pair: (pair[1].get("recorded", ""), pair[0]), reverse=True)
        return [item for _, item in found]

    def setting(self):
        """The pickup setting the phrase in use was recorded on, if it was noted and the phrase was recorded on the
        instrument now chosen."""
        studio = self.studio
        if not studio.phrase:
            return None
        found = next((item for item in self.recorded() if item["id"] == studio.phrase[0]), None)
        if found is not None and found.get("instrument") not in (None, studio.state.instrument):
            return None                   # a phrase recorded on another instrument says nothing of this one's settings
        setting = (found or {}).get("setting")
        return None if not setting or setting == "unknown" else setting

    def takes(self, instrument=None):
        """The takes recorded on an instrument with their pickup setting noted, as (phrase id, setting, the dry
        take's file, the gesture it was marked with or None)."""
        studio = self.studio
        instrument = instrument or studio.state.instrument
        return tuple((item["id"], item["setting"], studio.folder / "phrases" / item["id"] / "dry.wav",
                      item.get("gesture") or None)
                     for item in self.recorded()
                     if item.get("instrument") == instrument and item.get("setting") not in (None, "unknown"))

    # ---- what the instrument puts out ----

    def profile(self, instrument, takes):
        """What an instrument puts out at each pickup setting, from these takes, as (profile, lines). Reading the
        takes takes a second or more, so this runs on a worker; what it finds is kept until the takes change."""
        from lab.hearing.audio import read_wave
        from lab.hearing.profile import profile

        made_from = _made_from(takes)
        with self.lock:
            held = self.profiles.get(instrument)
        if held is not None and held[0] == made_from:
            return held[1]
        # The gesture goes with each take: two settings are compared only on takes of the same gesture.
        found = profile([(setting, read_wave(path)[:, :1], gesture) for _, setting, path, gesture in takes])
        with self.lock:
            self.profiles[instrument] = (made_from, found)
        return found

    def now(self):
        """What the current instrument puts out at each pickup setting, from the phrases recorded on it, as
        (profile, lines). Worked out here and now: the desk uses `guitar_lines`, which does not wait."""
        studio = self.studio
        takes = self.takes()
        if not takes:
            return {}, [f"No phrase has been recorded on the {studio.state.instrument} with its pickup setting noted."]
        return self.profile(studio.state.instrument, takes)

    def lines(self):
        """What the current instrument puts out, as lines for the Guitar panel. When it has not been worked out
        from the takes as they are now, that is started on a worker and the lines say so; `profile_version`
        changes when it arrives."""
        studio = self.studio
        instrument, takes = studio.state.instrument, self.takes()
        if not takes:
            return self.now()[1]
        made_from = _made_from(takes)
        held = self.profiles.get(instrument)
        if held is not None and held[0] == made_from:
            return [*held[1][1], *self.told(held[1][0])]
        if self.failed is not None and self.failed[0] == made_from:
            return [f"The takes could not be read: {self.failed[1]}"]
        if self.profiling != made_from:
            self.profiling = made_from

            def arrived(found=None, error=None):
                self.profiling = None
                self.failed = (made_from, str(error)) if error is not None else None
                self.version += 1
                studio._set()

            studio.later(studio.reading.submit(self.profile, instrument, takes), arrived,
                       lambda error: arrived(error=error))
        return [f"Working out what the {instrument} puts out from {len(takes)} takes…"]

    def summary(self):
        """What the current instrument puts out at each pickup setting, as worked out from its takes as they are
        now: {} with no takes, None while it is still being worked out (`lines` starts that)."""
        takes = self.takes()
        if not takes:
            return {}
        held = self.profiles.get(self.studio.state.instrument)
        return held[1][0] if held is not None and held[0] == _made_from(takes) else None

    def told(self, found):
        """What the designer is told of the pickup setting the phrase in use was recorded on (`profile.facts`),
        as lines: its level against what presets assume, and what it was, or could not be, compared on."""
        from lab.hearing.profile import facts

        setting = self.setting()
        said = (facts(found, setting) or {}).get("settings", {}) if setting else {}
        chosen = next((key for key in said if key.casefold() == setting.casefold()), None) if setting else None
        if chosen is None:
            return []
        return [f"With a request, the designer is told of the phrase in use ({chosen}): {said[chosen]['implies']}"]


def _made_from(takes):
    """What a profile was made from: each take, its setting and its gesture."""
    return tuple((phrase_id, setting, gesture) for phrase_id, setting, _, gesture in takes)
