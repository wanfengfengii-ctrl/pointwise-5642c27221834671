"""Offline store: received artifacts, fixed controls, signed reports.

Nothing here reads the source store.  An artifact file (claimed by number) is
first *received*; controls are registered from received artifacts; reports are
computed against a control and a list of ``(artifact, item)`` selections.

Report states
-------------
``signed``         the net integer displacement is determined;
``undetermined``   some ungrounded component can be flipped without changing the
                   total, so no absolute orientation exists and there is no net;
``contradiction``  a same/reverse contradiction lies in a component touched by
                   the selected items (证据矛盾) — no net.

``undetermined`` and ``contradiction`` carry ``net = None``.  Reports are
immutable; a report number always re-opens to exactly the value first signed.
"""

import time

from . import db
from .errors import CorruptError, DuplicateError, MissingError, ValidationError
from .graph import GROUND, ParityDSU
from .model import decode_record, digest_of, encode_record


SCHEMA = """
CREATE TABLE IF NOT EXISTS received (
    number      INTEGER PRIMARY KEY,
    payload     TEXT NOT NULL,
    digest      TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS controls (
    number      INTEGER PRIMARY KEY,
    payload     TEXT NOT NULL,
    digest      TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    number      INTEGER PRIMARY KEY,
    payload     TEXT NOT NULL,
    digest      TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS reports_no_update
    BEFORE UPDATE ON reports BEGIN SELECT RAISE(ABORT, 'reports are immutable'); END;
CREATE TRIGGER IF NOT EXISTS reports_no_delete
    BEFORE DELETE ON reports BEGIN SELECT RAISE(ABORT, 'reports are immutable'); END;
CREATE TRIGGER IF NOT EXISTS controls_no_update
    BEFORE UPDATE ON controls BEGIN SELECT RAISE(ABORT, 'controls are immutable'); END;
CREATE TRIGGER IF NOT EXISTS received_no_update
    BEFORE UPDATE ON received BEGIN SELECT RAISE(ABORT, 'received artifacts are immutable'); END;
"""


def open_store(path):
    conn = db.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# receive
# ---------------------------------------------------------------------------


def _validate_content(content):
    if not isinstance(content, dict):
        raise CorruptError("artifact content must be an object")
    terminals = content.get("terminals")
    items = content.get("items")
    edges = content.get("edges")
    if not isinstance(terminals, list) or not isinstance(items, list) or not isinstance(edges, list):
        raise CorruptError("artifact content needs terminal/item/edge lists")
    term_names = set()
    for t in terminals:
        if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t["name"]:
            raise CorruptError("malformed terminal entry")
        if t["name"] in term_names:
            raise CorruptError("duplicate terminal name")
        term_names.add(t["name"])
    item_names = set()
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("name"), str) or not it["name"]:
            raise CorruptError("malformed item entry")
        if it["name"] in item_names:
            raise CorruptError("duplicate item name")
        item_names.add(it["name"])
        if not isinstance(it.get("reading"), int) or isinstance(it.get("reading"), bool):
            raise CorruptError("item reading must be an integer")
    if term_names & item_names:
        raise CorruptError("a name cannot be both terminal and item")
    known = term_names | item_names
    for e in edges:
        if not isinstance(e, dict):
            raise CorruptError("malformed edge")
        a, b, sign = e.get("a"), e.get("b"), e.get("sign")
        if not isinstance(a, dict) or not isinstance(b, dict):
            raise CorruptError("malformed edge endpoint")
        if not isinstance(sign, int) or sign not in (-1, 1):
            raise CorruptError("edge sign must be ±1")
        for end in (a, b):
            if end.get("role") not in ("T", "I") or end.get("name") not in known:
                raise CorruptError("edge endpoint refers to unknown position")
    return content


def receive_artifact(offline_path, file_path):
    """Receive an artifact file; return its artifact number (idempotent)."""
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            doc = decode_record(fh.read())
    except (OSError, ValueError) as exc:
        raise CorruptError("cannot read artifact file: %s" % (exc,))

    if not isinstance(doc, dict) or doc.get("kind") != "artifact" or doc.get("v") != 1:
        raise CorruptError("not a version-1 artifact document")
    number = doc.get("artifact_number")
    if not isinstance(number, int) or number <= 0:
        raise CorruptError("artifact number must be a positive integer")
    content = doc.get("content")
    _validate_content(content)
    recomputed = digest_of(content)
    if doc.get("content_digest") != recomputed:
        raise CorruptError("artifact %d content digest mismatch (bad file)" % number)

    payload = {
        "artifact_number": number,
        "content": content,
        "content_digest": recomputed,
    }
    text, digest = encode_record(payload)

    conn = open_store(offline_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT digest FROM received WHERE number=?", (number,)
            ).fetchone()
            if row is not None:
                if row["digest"] == digest:
                    return number
                raise DuplicateError(
                    "artifact %d already received with different content" % number
                )
            conn.execute(
                "INSERT INTO received(number, payload, digest, created_at) VALUES (?,?,?,?)",
                (number, text, digest, time.time()),
            )
    finally:
        conn.close()
    return number


def _received(conn, number):
    row = conn.execute(
        "SELECT payload FROM received WHERE number=?", (number,)
    ).fetchone()
    if row is None:
        raise MissingError("artifact %r not received offline" % (number,))
    return decode_record(row["payload"])["content"]


# ---------------------------------------------------------------------------
# control
# ---------------------------------------------------------------------------


def register_control(offline_path, artifact_numbers, shared_edges, absolutes):
    """Register a fixed control from received artifacts; return its number."""
    numbers = list(dict.fromkeys(int(n) for n in artifact_numbers))
    if not numbers:
        raise ValidationError("control needs at least one artifact")

    conn = open_store(offline_path)
    try:
        # Validate every named artifact is received.
        contents = {n: _received(conn, n) for n in numbers}
        terminal_sets = {n: {t["name"] for t in contents[n]["terminals"]} for n in numbers}

        def check_terminal(art, term, where):
            if art not in terminal_sets:
                raise ValidationError(
                    "%s references artifact %r not listed in the control" % (where, art)
                )
            if term not in terminal_sets[art]:
                raise ValidationError(
                    "%s references terminal %r not listed on artifact %d"
                    % (where, term, art)
                )

        norm_shared = []
        for a, ta, b, tb, sign in shared_edges:
            check_terminal(a, ta, "shared edge")
            check_terminal(b, tb, "shared edge")
            if a == b and ta == tb:
                raise ValidationError("shared edge needs two distinct endpoints")
            norm_shared.append({"a": a, "ta": ta, "b": b, "tb": tb, "sign": sign})

        norm_abs = []
        for a, term, sign in absolutes:
            check_terminal(a, term, "absolute")
            norm_abs.append({"artifact": a, "terminal": term, "sign": sign})

        payload = {
            "artifacts": numbers,
            "artifact_digests": {
                str(n): digest_of(contents[n]) for n in numbers
            },
            "shared": norm_shared,
            "absolutes": norm_abs,
        }
        text, digest = encode_record(payload)
        with conn:
            number = conn.execute(
                "SELECT COALESCE(MAX(number), 0) AS m FROM controls"
            ).fetchone()["m"] + 1
            conn.execute(
                "INSERT INTO controls(number, payload, digest, created_at) VALUES (?,?,?,?)",
                (number, text, digest, time.time()),
            )
    finally:
        conn.close()
    return number


def get_control(offline_path, number):
    conn = open_store(offline_path)
    try:
        row = conn.execute(
            "SELECT payload FROM controls WHERE number=?", (number,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise MissingError("unknown control %r" % (number,))
    return decode_record(row["payload"])


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _build_graph(conn, control):
    """Rebuild the parity graph (terminals + items + shared edges + absolutes)."""
    dsu = ParityDSU()
    dsu.add(GROUND)
    contents = {}
    for number in control["artifacts"]:
        content = _received(conn, number)
        contents[number] = content
        for t in content["terminals"]:
            dsu.add((number, "T", t["name"]))
        for it in content["items"]:
            dsu.add((number, "I", it["name"]))
        for e in content["edges"]:
            na = (number, e["a"]["role"], e["a"]["name"])
            nb = (number, e["b"]["role"], e["b"]["name"])
            dsu.union(na, nb, e["sign"])
    for e in control["shared"]:
        na = (e["a"], "T", e["ta"])
        nb = (e["b"], "T", e["tb"])
        dsu.union(na, nb, e["sign"])
    for ab in control["absolutes"]:
        dsu.union((ab["artifact"], "T", ab["terminal"]), GROUND, ab["sign"])
    return dsu, contents


def _readings(conn, control):
    readings = {}
    for number in control["artifacts"]:
        for it in _received(conn, number)["items"]:
            readings[(number, it["name"])] = it["reading"]
    return readings


def compute_report(conn, control_number, selections):
    """Compute the report body without writing it."""
    control = get_control_by_conn(conn, control_number)
    listed = set(control["artifacts"])

    if not selections:
        raise ValidationError("selection list must be non-empty")
    seen = set()
    for artifact, item in selections:
        if artifact not in listed:
            raise ValidationError(
                "selection artifact %r is not listed in control %d" % (artifact, control_number)
            )
        key = (artifact, item)
        if key in seen:
            raise ValidationError("duplicate selection %r:%r" % key)
        seen.add(key)

    readings = _readings(conn, control)
    for artifact, item in selections:
        if (artifact, item) not in readings:
            raise ValidationError(
                "item %r is not a test item of artifact %d" % (item, artifact)
            )

    dsu, _contents = _build_graph(conn, control)

    # Contradiction check limited to components touched by selected items.
    touched_roots = set()
    for artifact, item in selections:
        root, _ = dsu.find((artifact, "I", item))
        touched_roots.add(root)
        if dsu.contradict((artifact, "I", item)):
            return {
                "state": "contradiction",
                "net": None,
                "reason": "same/reverse contradiction in a touched component",
                "components": [],
            }

    # Group selected items by component root.
    groups = {}
    for artifact, item in selections:
        root, p = dsu.find((artifact, "I", item))
        groups.setdefault(root, []).append((artifact, item, p))

    grounded_total = 0
    ungrounded = []
    details = []
    for root, members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        grounded = dsu.grounded_root() == root
        if grounded:
            ground_pg = dsu.find(GROUND)[1]
            subtotal = 0
            for artifact, item, p in members:
                absolute_sign = p * ground_pg
                subtotal += readings[(artifact, item)] * absolute_sign
            grounded_total += subtotal
            details.append({
                "component": len(details),
                "grounded": True,
                "contribution": subtotal,
            })
        else:
            c = 0
            for artifact, item, p in members:
                c += readings[(artifact, item)] * p
            ungrounded.append(c)
            details.append({
                "component": len(details),
                "grounded": False,
                "flip_sum": c,
            })

    if any(c != 0 for c in ungrounded):
        return {
            "state": "undetermined",
            "net": None,
            "reason": "an ungrounded component can flip without fixing the total",
            "components": details,
        }

    return {
        "state": "signed",
        "net": grounded_total,
        "reason": "net displacement determined",
        "components": details,
    }


def get_control_by_conn(conn, number):
    row = conn.execute(
        "SELECT payload FROM controls WHERE number=?", (number,)
    ).fetchone()
    if row is None:
        raise MissingError("unknown control %r" % (number,))
    return decode_record(row["payload"])


def create_report(offline_path, control_number, selections):
    """Compute and store a report; return its number.  Nothing is written on refusal."""
    conn = open_store(offline_path)
    try:
        control = get_control_by_conn(conn, control_number)
        _ = control  # validate control exists before anything else

        body = compute_report(conn, control_number, selections)
        payload = {
            "control": control_number,
            "selections": [
                {"artifact": a, "item": i} for a, i in selections
            ],
            "parents": sorted({a for a, _ in selections}),
            "state": body["state"],
            "net": body["net"],
            "reason": body["reason"],
            "components": body["components"],
        }
        text, digest = encode_record(payload)
        with conn:
            number = conn.execute(
                "SELECT COALESCE(MAX(number), 0) AS m FROM reports"
            ).fetchone()["m"] + 1
            conn.execute(
                "INSERT INTO reports(number, payload, digest, created_at) VALUES (?,?,?,?)",
                (number, text, digest, time.time()),
            )
    finally:
        conn.close()

    result = dict(payload)
    result["report_number"] = number
    result["digest"] = digest
    return result


def get_report(offline_path, number):
    conn = open_store(offline_path)
    try:
        row = conn.execute(
            "SELECT number, payload, digest FROM reports WHERE number=?", (number,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise MissingError("unknown report %r" % (number,))
    payload = decode_record(row["payload"])
    payload["report_number"] = row["number"]
    payload["digest"] = row["digest"]
    return payload


def list_reports(offline_path):
    conn = open_store(offline_path)
    try:
        rows = conn.execute(
            "SELECT number, digest FROM reports ORDER BY number"
        ).fetchall()
    finally:
        conn.close()
    return [{"number": r["number"], "digest": r["digest"]} for r in rows]
