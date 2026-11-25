"""Source store: immutable sheet revisions and re-issuable sheet artifacts.

Layout (one SQLite file):

``revisions``  append-only imports.  Each row freezes a whole sheet: name, unit,
               measurements ``(orig_id, reference, reading)`` and reference
               direction edges ``(ref_a, ref_b, ±1)``.  Rows are immutable
               (UPDATE/DELETE blocked by triggers).
``artifacts``  public projections of a revision.  An artifact exposes only
               public terminals, public test items (with readings) and the
               same/reverse relations *derivable between public positions*.
               Original measurement ids and internal reference names never
               leave the source.  Artifacts are immutable and claimable by id.

Validation performed on import
------------------------------
* unit must be micrometres;
* an original measurement id may appear only once per import (原测重号);
* the reference direction graph must be internally consistent (源内方向矛盾).

Artifact issue refuses (MissingError) an unknown revision or a mapping whose
target does not exist in the chosen revision.
"""

import time

from . import db
from .errors import ConflictError, MissingError, ValidationError
from .graph import ParityDSU
from .model import (
    decode_record,
    digest_of,
    encode_record,
    normalize_unit,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS revisions (
    number      INTEGER PRIMARY KEY,
    sheet       TEXT NOT NULL,
    unit        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    digest      TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    number      INTEGER PRIMARY KEY,
    revision    INTEGER NOT NULL,
    issue_key   TEXT NOT NULL,
    payload     TEXT NOT NULL,
    digest      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (revision) REFERENCES revisions(number)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_artifacts_issue ON artifacts(issue_key);
CREATE TRIGGER IF NOT EXISTS revisions_no_update
    BEFORE UPDATE ON revisions BEGIN SELECT RAISE(ABORT, 'revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS revisions_no_delete
    BEFORE DELETE ON revisions BEGIN SELECT RAISE(ABORT, 'revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS artifacts_no_update
    BEFORE UPDATE ON artifacts BEGIN SELECT RAISE(ABORT, 'artifacts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS artifacts_no_delete
    BEFORE DELETE ON artifacts BEGIN SELECT RAISE(ABORT, 'artifacts are immutable'); END;
"""


def open_store(path):
    conn = db.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def import_sheet(path, sheet, unit, measures, edges):
    """Import one sheet; return the new immutable revision number.

    ``measures`` is a list of ``(orig_id, reference, reading)`` already parsed
    from strings; ``edges`` a list of ``(ref_a, ref_b, sign)``.
    """
    if not sheet or not sheet.strip():
        raise ValidationError("sheet name is required")
    sheet = sheet.strip()
    unit = normalize_unit(unit)

    if not measures:
        raise ValidationError("at least one measurement is required")

    seen = set()
    norm_measures = []
    for orig, ref, reading in measures:
        if not orig or not ref:
            raise ValidationError("measurement id and reference must be non-empty")
        if orig in seen:
            raise ValidationError("duplicate original measurement id %r" % (orig,))
        seen.add(orig)
        norm_measures.append({"orig": orig, "reference": ref, "reading": int(reading)})

    # Validate direction graph consistency.
    dsu = ParityDSU()
    edge_rows = []
    for ref_a, ref_b, sign in edges:
        if not (sign == 1 or sign == -1):
            raise ConflictError("edge sign must be ±1")
        ok = dsu.union(ref_a, ref_b, sign)
        if not ok:
            raise ConflictError(
                "source direction contradiction on %r/%r" % (ref_a, ref_b)
            )
        edge_rows.append({"a": ref_a, "b": ref_b, "sign": sign})

    payload = {
        "sheet": sheet,
        "unit": unit,
        "measures": norm_measures,
        "edges": edge_rows,
    }
    text, digest = encode_record(payload)

    conn = open_store(path)
    try:
        with conn:
            row = conn.execute("SELECT COALESCE(MAX(number), 0) AS m FROM revisions").fetchone()
            number = row["m"] + 1
            conn.execute(
                "INSERT INTO revisions(number, sheet, unit, payload, digest, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (number, sheet, unit, text, digest, time.time()),
            )
    finally:
        conn.close()
    return number


def get_revision(path, number):
    conn = open_store(path)
    try:
        row = conn.execute(
            "SELECT number, sheet, unit, payload, digest FROM revisions WHERE number=?",
            (number,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise MissingError("unknown revision %r" % (number,))
    payload = decode_record(row["payload"])
    payload.update({"number": row["number"], "sheet": row["sheet"], "unit": row["unit"]})
    return payload


# ---------------------------------------------------------------------------
# artifact issue
# ---------------------------------------------------------------------------


def issue_artifact(path, revision_number, item_map, terminal_map):
    """Issue an artifact for ``revision_number``.

    ``item_map``     : public test-item name -> original measurement id
    ``terminal_map`` : public terminal name -> internal reference name

    Returns the artifact number (idempotent for identical requests).
    """
    if not item_map and not terminal_map:
        raise ValidationError("artifact needs at least one terminal or test item")

    rev = get_revision(path, revision_number)

    if set(item_map) & set(terminal_map):
        raise ValidationError("a name cannot be both a terminal and a test item")

    measures = {m["orig"]: m for m in rev["measures"]}
    known_refs = set()
    for m in rev["measures"]:
        known_refs.add(m["reference"])
    for e in rev["edges"]:
        known_refs.add(e["a"])
        known_refs.add(e["b"])

    # Resolve mapping targets first (unknown -> missing).
    for item, orig in item_map.items():
        if orig not in measures:
            raise MissingError("unknown measurement %r for item %r" % (orig, item))
    for term, ref in terminal_map.items():
        if ref not in known_refs:
            raise MissingError("unknown reference %r for terminal %r" % (ref, term))

    # Build the reference parity graph of the revision.
    dsu = ParityDSU()
    for e in rev["edges"]:
        dsu.union(e["a"], e["b"], e["sign"])

    # Public nodes -> (kind, name) with their source reference.
    nodes = []  # (kind, name, ref, reading|None)
    for term in sorted(terminal_map):
        nodes.append(("T", term, terminal_map[term], None))
    for item in sorted(item_map):
        m = measures[item_map[item]]
        nodes.append(("I", item, m["reference"], m["reading"]))

    # Deduce pairwise parity between public nodes sharing a component.
    roots = {}
    for kind, name, ref, _reading in nodes:
        root, p = dsu.find(ref)
        roots.setdefault(root, []).append((kind, name, p))

    edges_out = []
    for root, members in roots.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                ka, na, pa = members[i]
                kb, nb, pb = members[j]
                edges_out.append(
                    {"a": {"role": ka, "name": na},
                     "b": {"role": kb, "name": nb},
                     "sign": pa * pb}
                )
    edges_out.sort(key=lambda e: (
        e["a"]["role"], e["a"]["name"], e["b"]["role"], e["b"]["name"]))

    content = {
        "sheet": rev["sheet"],
        "terminals": [{"name": t} for t in sorted(terminal_map)],
        "items": [
            {"name": item, "reading": measures[item_map[item]]["reading"]}
            for item in sorted(item_map)
        ],
        "edges": edges_out,
    }
    content_digest = digest_of(content)

    issue_key = digest_of({
        "revision": revision_number,
        "items": sorted(item_map.items()),
        "terminals": sorted(terminal_map.items()),
    })

    conn = open_store(path)
    try:
        with conn:
            existing = conn.execute(
                "SELECT number FROM artifacts WHERE issue_key=?", (issue_key,)
            ).fetchone()
            if existing is not None:
                return existing["number"]
            number = conn.execute(
                "SELECT COALESCE(MAX(number), 0) AS m FROM artifacts"
            ).fetchone()["m"] + 1
            payload = {
                "artifact_number": number,
                "revision": revision_number,
                "content": content,
                "content_digest": content_digest,
            }
            text, digest = encode_record(payload)
            conn.execute(
                "INSERT INTO artifacts(number, revision, issue_key, payload, digest, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (number, revision_number, issue_key, text, digest, time.time()),
            )
    finally:
        conn.close()
    return number


def artifact_file(path, number):
    """Return the portable artifact document for ``number`` (claim by id)."""
    conn = open_store(path)
    try:
        row = conn.execute(
            "SELECT payload FROM artifacts WHERE number=?", (number,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise MissingError("unknown artifact %r" % (number,))
    payload = decode_record(row["payload"])
    return {
        "kind": "artifact",
        "v": 1,
        "artifact_number": payload["artifact_number"],
        "content": payload["content"],
        "content_digest": payload["content_digest"],
    }


def list_revisions(path):
    conn = open_store(path)
    try:
        rows = conn.execute("SELECT number, sheet, digest FROM revisions ORDER BY number").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def list_artifacts(path):
    conn = open_store(path)
    try:
        rows = conn.execute(
            "SELECT number, revision, digest FROM artifacts ORDER BY number"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
