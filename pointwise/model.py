"""Canonical JSON, digests and small parsing helpers shared by both stores."""

import hashlib
import json

from .errors import ValidationError

# ---------------------------------------------------------------------------
# canonical serialisation and integrity
# ---------------------------------------------------------------------------


def canonical(obj):
    """Deterministic JSON text used for hashing and storage."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(payload):
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def encode_record(payload):
    """Return (payload_text, digest) for storage."""
    return canonical(payload), digest_of(payload)


def decode_record(text):
    return json.loads(text)


def artifact_file(payload):
    """Portable on-disk artifact document."""
    return {"kind": "artifact-file", "v": 1, "payload": payload, "digest": digest_of(payload)}


# ---------------------------------------------------------------------------
# units
# ---------------------------------------------------------------------------

# Accept the common spellings of a micrometre.  Everything else is refused.
_MICRO_UNITS = {
    "微米",
    "µm",  # U+00B5 micro sign
    "μm",  # U+03BC greek mu
    "um",
    "micron",
    "microns",
    "micrometer",
    "micrometers",
    "micrometre",
    "micrometres",
}


def normalize_unit(raw):
    if raw is None:
        raise ValidationError("unit is required")
    value = raw.strip()
    key = value.lower().replace("μ", "µ")
    if key not in _MICRO_UNITS:
        raise ValidationError("unit must be micrometres (微米), got %r" % (raw,))
    return "µm"


# ---------------------------------------------------------------------------
# primitive parsers
# ---------------------------------------------------------------------------


def parse_int(raw, what="value"):
    if isinstance(raw, bool):
        raise ValidationError("%s must be an integer" % what)
    if isinstance(raw, int):
        return raw
    try:
        text = str(raw).strip()
        if text == "":
            raise ValueError
        return int(text, 10)
    except (TypeError, ValueError):
        raise ValidationError("%s must be an integer, got %r" % (what, raw))


def parse_sign(raw, what="sign"):
    value = parse_int(raw, what)
    if value not in (-1, 1):
        raise ValidationError("%s must be +1 or -1, got %r" % (what, raw))
    return value


# ---------------------------------------------------------------------------
# argument micro-syntax
# ---------------------------------------------------------------------------


def split_measure(spec):
    """``ORIG:REFERENCE:READING`` -> (orig, reference, reading)."""
    parts = spec.split(":")
    if len(parts) != 3:
        raise ValidationError(
            "measurement must be ORIG:REFERENCE:READING, got %r" % (spec,)
        )
    orig, reference, reading = parts
    if not orig or not reference:
        raise ValidationError("measurement original id and reference must be non-empty")
    return orig, reference, parse_int(reading, "reading")


def split_edge(spec):
    """``REF_A:REF_B:SIGN`` -> (ref_a, ref_b, sign)."""
    parts = spec.split(":")
    if len(parts) != 3:
        raise ValidationError("edge must be REF:REF:SIGN, got %r" % (spec,))
    a, b, sign = parts
    if not a or not b:
        raise ValidationError("edge endpoints must be non-empty")
    return a, b, parse_sign(sign, "edge sign")


def split_named_ref(spec):
    """``TERMINAL:REF`` -> (terminal, ref)."""
    parts = spec.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValidationError("terminal must be TERMINAL:REF, got %r" % (spec,))
    return parts[0], parts[1]


def split_named_orig(spec):
    """``ITEM:ORIG`` -> (item, orig)."""
    parts = spec.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValidationError("item must be ITEM:ORIG, got %r" % (spec,))
    return parts[0], parts[1]


def split_shared(spec):
    """``A:TA=B:TB:SIGN`` -> (a, ta, b, tb, sign)."""
    if "=" not in spec:
        raise ValidationError("shared edge must be A:TA=B:TB:SIGN, got %r" % (spec,))
    left, right = spec.split("=", 1)
    if ":" not in left or ":" not in right:
        raise ValidationError("shared edge endpoints need ARTIFACT:TERMINAL")
    a, ta = left.split(":", 1)
    b_part, sign_raw = right.rsplit(":", 1)
    if ":" not in b_part:
        raise ValidationError("shared edge needs ARTIFACT:TERMINAL on both sides")
    b, tb = b_part.rsplit(":", 1)
    if not all((a, ta, b, tb)):
        raise ValidationError("shared edge parts must be non-empty")
    return int(a), ta, int(b), tb, parse_sign(sign_raw, "shared sign")


def split_absolute(spec):
    """``A:TERMINAL:SIGN`` -> (artifact, terminal, sign)."""
    parts = spec.rsplit(":", 2)
    if len(parts) != 3 or not parts[0] or not parts[1]:
        raise ValidationError("absolute must be ARTIFACT:TERMINAL:SIGN, got %r" % (spec,))
    artifact, terminal, sign = parts
    return int(artifact), terminal, parse_sign(sign, "absolute sign")


def split_artifact_item(spec):
    """``ARTIFACT:ITEM`` -> (artifact, item)."""
    parts = spec.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValidationError("selection must be ARTIFACT:ITEM, got %r" % (spec,))
    return int(parts[0]), parts[1]
