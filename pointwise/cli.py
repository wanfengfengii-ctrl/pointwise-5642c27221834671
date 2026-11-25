"""Command line interface: ``python3 -m pointwise <group> <command> ...``.

Every command prints one canonical JSON object on stdout.  Failures print a JSON
object with ``error``/``code`` on stderr and exit non-zero.
"""

import argparse
import json
import sys

from . import offline, source
from .errors import PointwiseError, ValidationError
from .model import (
    split_absolute,
    split_artifact_item,
    split_edge,
    split_measure,
    split_named_orig,
    split_named_ref,
    split_shared,
)


def _emit(obj):
    sys.stdout.write(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")


def _write_file(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.write("\n")


# ---------------------------------------------------------------------------
# source commands
# ---------------------------------------------------------------------------


def cmd_source_import(args):
    measures = [split_measure(m) for m in (args.measure or [])]
    edges = [split_edge(e) for e in (args.edge or [])]
    number = source.import_sheet(args.db, args.sheet, args.unit, measures, edges)
    _emit({"revision": number})


def cmd_source_revisions(args):
    _emit({"revisions": source.list_revisions(args.db)})


def cmd_source_revision(args):
    rev = source.get_revision(args.db, args.revision)
    _emit(rev)


def cmd_source_issue(args):
    items = [split_named_orig(s) for s in (args.item or [])]
    terms = [split_named_ref(s) for s in (args.terminal or [])]
    item_map = {}
    for name, orig in items:
        if name in item_map:
            raise ValidationError("duplicate public item name %r" % (name,))
        item_map[name] = orig
    term_map = {}
    for name, ref in terms:
        if name in term_map:
            raise ValidationError("duplicate public terminal name %r" % (name,))
        term_map[name] = ref
    number = source.issue_artifact(args.db, args.revision, item_map, term_map)
    _emit({"artifact": number})


def cmd_source_artifacts(args):
    _emit({"artifacts": source.list_artifacts(args.db)})


def cmd_source_pull(args):
    doc = source.artifact_file(args.db, args.artifact)
    payload = {"kind": "artifact-file", "v": 1,
               "artifact_number": doc["artifact_number"],
               "content": doc["content"],
               "content_digest": doc["content_digest"]}
    text = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if args.out:
        _write_file(args.out, text)
        _emit({"artifact": doc["artifact_number"], "file": args.out})
    else:
        _emit(doc)


# ---------------------------------------------------------------------------
# offline commands
# ---------------------------------------------------------------------------


def cmd_offline_receive(args):
    number = offline.receive_artifact(args.db, args.file)
    _emit({"artifact": number})


def cmd_offline_control(args):
    shared = [split_shared(s) for s in (args.shared or [])]
    absolutes = [split_absolute(s) for s in (args.absolute or [])]
    number = offline.register_control(args.db, args.artifact or [], shared, absolutes)
    _emit({"control": number})


def cmd_offline_control_get(args):
    _emit(offline.get_control(args.db, args.control))


def cmd_offline_report(args):
    selections = [split_artifact_item(s) for s in (args.select or [])]
    result = offline.create_report(args.db, args.control, selections)
    if args.out:
        text = json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        _write_file(args.out, text)
        _emit({"report": result["report_number"], "state": result["state"],
               "net": result["net"], "file": args.out})
    else:
        _emit(result)


def cmd_offline_report_get(args):
    _emit(offline.get_report(args.db, args.report))


def cmd_offline_reports(args):
    _emit({"reports": offline.list_reports(args.db)})


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(prog="pointwise", description=__doc__)
    groups = parser.add_subparsers(dest="group", required=True)

    src = groups.add_parser("source", help="source store operations")
    src_cmds = src.add_subparsers(dest="command", required=True)

    p = src_cmds.add_parser("import", help="import a sheet as an immutable revision")
    p.add_argument("--db", required=True)
    p.add_argument("--sheet", required=True)
    p.add_argument("--unit", required=True)
    p.add_argument("--measure", action="append", default=[], metavar="ORIG:REFERENCE:READING")
    p.add_argument("--edge", action="append", default=[], metavar="REF:REF:SIGN")
    p.set_defaults(func=cmd_source_import)

    p = src_cmds.add_parser("revisions", help="list revisions")
    p.add_argument("--db", required=True)
    p.set_defaults(func=cmd_source_revisions)

    p = src_cmds.add_parser("revision", help="show one revision")
    p.add_argument("--db", required=True)
    p.add_argument("--revision", required=True, type=int)
    p.set_defaults(func=cmd_source_revision)

    p = src_cmds.add_parser("issue", help="issue a sheet artifact")
    p.add_argument("--db", required=True)
    p.add_argument("--revision", required=True, type=int)
    p.add_argument("--item", action="append", default=[], metavar="PUBLIC:ORIG")
    p.add_argument("--terminal", action="append", default=[], metavar="PUBLIC:REF")
    p.set_defaults(func=cmd_source_issue)

    p = src_cmds.add_parser("artifacts", help="list issued artifacts")
    p.add_argument("--db", required=True)
    p.set_defaults(func=cmd_source_artifacts)

    p = src_cmds.add_parser("pull", help="re-claim an artifact by number")
    p.add_argument("--db", required=True)
    p.add_argument("--artifact", required=True, type=int)
    p.add_argument("--out")
    p.set_defaults(func=cmd_source_pull)

    off = groups.add_parser("offline", help="offline store operations")
    off_cmds = off.add_subparsers(dest="command", required=True)

    p = off_cmds.add_parser("receive", help="receive an artifact file")
    p.add_argument("--db", required=True)
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_offline_receive)

    p = off_cmds.add_parser("control", help="register a fixed control")
    p.add_argument("--db", required=True)
    p.add_argument("--artifact", action="append", type=int, default=[], metavar="N")
    p.add_argument("--shared", action="append", default=[], metavar="A:TA=B:TB:SIGN")
    p.add_argument("--absolute", action="append", default=[], metavar="A:TERM:SIGN")
    p.set_defaults(func=cmd_offline_control)

    p = off_cmds.add_parser("control-get", help="show a control by number")
    p.add_argument("--db", required=True)
    p.add_argument("--control", required=True, type=int)
    p.set_defaults(func=cmd_offline_control_get)

    p = off_cmds.add_parser("report", help="compute and sign a report")
    p.add_argument("--db", required=True)
    p.add_argument("--control", required=True, type=int)
    p.add_argument("--select", action="append", default=[], metavar="ARTIFACT:ITEM")
    p.add_argument("--out")
    p.set_defaults(func=cmd_offline_report)

    p = off_cmds.add_parser("report-get", help="claim a report by number")
    p.add_argument("--db", required=True)
    p.add_argument("--report", required=True, type=int)
    p.set_defaults(func=cmd_offline_report_get)

    p = off_cmds.add_parser("reports", help="list report numbers")
    p.add_argument("--db", required=True)
    p.set_defaults(func=cmd_offline_reports)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except PointwiseError as exc:
        sys.stderr.write(json.dumps(
            {"error": exc.message, "code": exc.code, **exc.details},
            sort_keys=True, ensure_ascii=False,
        ) + "\n")
        return exc.exit_code
    return 0
