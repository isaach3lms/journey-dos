"""Keys, transposition, and capos.

Pure functions, no database, no Flask. Worth its own module because getting
this wrong is the kind of error a worship leader notices in front of a
congregation, and because the correct answer is not simply "add two semitones".

**Spelling matters.** A♯ and B♭ are the same pitch and are not the same key. A
band handed "A# major" will stop and ask, because nobody writes it: that key
signature has ten sharps including double sharps. The conventional spelling for
each pitch differs between major and minor keys, so both tables are here.

**Capos are the point for most churches.** A volunteer guitarist who owns three
chord shapes can play anything if told "capo 2, play in G". Giving them the
sounding key alone is technically complete and practically useless, which is
why `capo_options` exists.

No lyrics are stored as text anywhere in this system, and charts only as the
PDF a church attached under its own SongSelect licence (app/models/songchart.py).
This module handles the key, and the `ccli_number` on the song points at the
church's own licensed copy.
"""

from __future__ import annotations

from dataclasses import dataclass

# Semitones above C.
PITCH_CLASSES = {
    "C": 0, "B#": 0,
    "C#": 1, "Db": 1,
    "D": 2,
    "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4,
    "F": 5, "E#": 5,
    "F#": 6, "Gb": 6,
    "G": 7,
    "G#": 8, "Ab": 8,
    "A": 9,
    "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}

# How each pitch is conventionally written as a major key. F# rather than Gb
# because six sharps beats six flats in a band folder, and Db rather than C#
# because five flats beats seven sharps.
MAJOR_SPELLING = (
    "C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B",
)

# Minor keys spell differently. Bbm rather than A#m, Ebm rather than D#m.
MINOR_SPELLING = (
    "C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B",
)

# Guitar shapes a volunteer is likely to own, in the order most would try.
FRIENDLY_GUITAR_KEYS = ("G", "D", "A", "E", "C")

MAX_CAPO = 7


class UnknownKey(ValueError):
    """Raised with the input, so the message names what was actually typed."""


@dataclass(frozen=True)
class Key:
    pitch_class: int
    is_minor: bool

    def __str__(self) -> str:
        table = MINOR_SPELLING if self.is_minor else MAJOR_SPELLING
        return table[self.pitch_class % 12] + ("m" if self.is_minor else "")

    @property
    def label(self) -> str:
        table = MINOR_SPELLING if self.is_minor else MAJOR_SPELLING
        root = table[self.pitch_class % 12]
        return f"{root} minor" if self.is_minor else f"{root} major"


def parse_key(raw: str | None) -> Key:
    """Accept what a worship leader would actually type: G, Bb, F#m, Am, a."""
    if not raw:
        raise UnknownKey("No key given.")

    text = raw.strip().replace("♯", "#").replace("♭", "b")
    if not text:
        raise UnknownKey("No key given.")

    is_minor = False
    for suffix in ("minor", "min", "m"):
        if text.lower().endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)].strip()
            is_minor = True
            break

    # Deliberately NOT treating a lone lowercase letter as minor.
    #
    # In classical notation "a" means A minor, and the first version of this
    # module honored that. It is wrong for the people who will use it. A
    # volunteer worship leader typing "g" into a key field means G major
    # essentially always, and silently recording G minor would hand a band the
    # wrong key with nothing on screen to reveal it. Minor has to be said out
    # loud: Gm, Gmin, or G minor.

    root = text[0].upper() + text[1:].replace("B", "b")
    if root not in PITCH_CLASSES:
        raise UnknownKey(f"{raw!r} is not a key. Try G, Bb, F#m, or Am.")

    return Key(PITCH_CLASSES[root], is_minor)


def transpose(raw: str, semitones: int) -> Key:
    """Move a key by semitones, wrapping at the octave."""
    key = parse_key(raw)
    return Key((key.pitch_class + semitones) % 12, key.is_minor)


def interval_between(from_key: str, to_key: str) -> int:
    """Semitones from one key to another, taking the shorter way round.

    Returns -5 rather than +7, because a band told "down a fourth" reaches for
    something different from "up a fifth" even though the pitch is the same.
    """
    start, end = parse_key(from_key), parse_key(to_key)
    delta = (end.pitch_class - start.pitch_class) % 12
    return delta - 12 if delta > 6 else delta


@dataclass(frozen=True)
class CapoOption:
    capo: int
    shape: str          # the key the guitarist plays in
    sounds_like: str    # what the room hears


def capo_options(sounding_key: str, limit: int = 3) -> list[CapoOption]:
    """Ways to play `sounding_key` with easy shapes.

    A capo raises pitch, so a shape played at fret N sounds N semitones higher.
    To sound in A with a capo on 2, the guitarist plays G shapes.

    Ordered by how common the shape is, then by the lowest capo, because a
    volunteer would rather use a shape they know than a lower fret.
    """
    target = parse_key(sounding_key)
    options: list[CapoOption] = []

    for capo in range(0, MAX_CAPO + 1):
        shape = Key((target.pitch_class - capo) % 12, target.is_minor)
        shape_root = str(shape).rstrip("m")
        if shape_root in FRIENDLY_GUITAR_KEYS:
            options.append(
                CapoOption(capo=capo, shape=str(shape), sounds_like=str(target))
            )

    options.sort(
        key=lambda option: (
            FRIENDLY_GUITAR_KEYS.index(option.shape.rstrip("m")),
            option.capo,
        )
    )
    return options[:limit]


def key_choices() -> list[str]:
    """Every key, for a dropdown. Majors first, the order a musician expects."""
    return [f"{root}" for root in MAJOR_SPELLING] + [
        f"{root}m" for root in MINOR_SPELLING
    ]


def normalize_key(raw: str | None) -> str | None:
    """Clean a typed key into its conventional spelling, or None if empty."""
    if not raw or not raw.strip():
        return None
    return str(parse_key(raw))
