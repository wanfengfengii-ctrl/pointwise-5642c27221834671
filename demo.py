#!/usr/bin/env python3
"""End-to-end demonstration.

Walks the full lifecycle: import sheet revisions into a source store, issue
sheet artifacts, pull them by number, receive them offline, register fixed
controls and sign reports.  The source store is then *withdrawn* and the
offline store keeps signing and re-claiming reports by number without it.

Prints a JSON transcript.  Run with:  python3 -B demo.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))


def cli(args):
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "pointwise"] + args,
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit("command failed: %r\n%s" % (args, proc.stderr))
    return json.loads(proc.stdout)


def main():
    work = tempfile.mkdtemp(prefix="pointwise-demo-")
    try:
        src = os.path.join(work, "source.db")
        off = os.path.join(work, "offline.db")
        steps = []

        # --- source: A and B, readings 7 and 7 -------------------------
        # A: u --same--> r (private) --reverse--> p ; p is the measurement ref
        rev_a = cli(["source", "import", "--db", src, "--sheet", "A", "--unit", "微米",
                     "--measure", "b1:p:7", "--edge", "u:r:+1", "--edge", "p:r:-1"])["revision"]
        # B: v --same--> q
        rev_b = cli(["source", "import", "--db", src, "--sheet", "B", "--unit", "微米",
                     "--measure", "b2:q:7", "--edge", "v:q:+1"])["revision"]
        steps.append({"import": {"A": rev_a, "B": rev_b}})

        art_a = cli(["source", "issue", "--db", src, "--revision", str(rev_a),
                     "--item", "p:b1", "--terminal", "u:u"])["artifact"]
        art_b = cli(["source", "issue", "--db", src, "--revision", str(rev_b),
                     "--item", "q:b2", "--terminal", "v:v"])["artifact"]
        steps.append({"issue": {"A": art_a, "B": art_b}})

        file_a = os.path.join(work, "artifact-%d.json" % art_a)
        file_b = os.path.join(work, "artifact-%d.json" % art_b)
        cli(["source", "pull", "--db", src, "--artifact", str(art_a), "--out", file_a])
        cli(["source", "pull", "--db", src, "--artifact", str(art_b), "--out", file_b])
        steps.append({"pulled": [os.path.basename(file_a), os.path.basename(file_b)]})

        # --- offline: receive and register controls --------------------
        cli(["offline", "receive", "--db", off, "--file", file_a])
        cli(["offline", "receive", "--db", off, "--file", file_b])

        # same-direction shared edge u = v  ->  p and q reverse -> net 0
        c_same = cli(["offline", "control", "--db", off,
                      "--artifact", str(art_a), "--artifact", str(art_b),
                      "--shared", "%d:u=%d:v:+1" % (art_a, art_b)])["control"]
        r_same = cli(["offline", "report", "--db", off, "--control", str(c_same),
                      "--select", "%d:p" % art_a, "--select", "%d:q" % art_b])
        steps.append({"same_direction": {"control": c_same, "report": r_same["report_number"],
                                         "state": r_same["state"], "net": r_same["net"]}})

        # no shared edge -> undetermined
        c_none = cli(["offline", "control", "--db", off,
                      "--artifact", str(art_a), "--artifact", str(art_b)])["control"]
        r_none = cli(["offline", "report", "--db", off, "--control", str(c_none),
                      "--select", "%d:p" % art_a, "--select", "%d:q" % art_b])
        steps.append({"no_shared_edge": {"control": c_none, "state": r_none["state"],
                                         "net": r_none["net"]}})

        # same + reverse coexist -> contradiction
        c_conf = cli(["offline", "control", "--db", off,
                      "--artifact", str(art_a), "--artifact", str(art_b),
                      "--shared", "%d:u=%d:v:+1" % (art_a, art_b),
                      "--shared", "%d:u=%d:v:-1" % (art_a, art_b)])["control"]
        r_conf = cli(["offline", "report", "--db", off, "--control", str(c_conf),
                      "--select", "%d:p" % art_a, "--select", "%d:q" % art_b])
        steps.append({"same_and_reverse": {"control": c_conf, "state": r_conf["state"],
                                           "net": r_conf["net"]}})

        # --- field revision of B to reading 5, same shared edge, v +1 ---
        rev_b2 = cli(["source", "import", "--db", src, "--sheet", "B", "--unit", "微米",
                      "--measure", "b3:q:5", "--edge", "v:q:+1"])["revision"]
        art_b2 = cli(["source", "issue", "--db", src, "--revision", str(rev_b2),
                      "--item", "q:b3", "--terminal", "v:v"])["artifact"]
        file_b2 = os.path.join(work, "artifact-%d.json" % art_b2)
        cli(["source", "pull", "--db", src, "--artifact", str(art_b2), "--out", file_b2])
        steps.append({"revised_B": {"revision": rev_b2, "artifact": art_b2}})

        # --- withdraw the source store ---------------------------------
        os.remove(src)
        steps.append({"source_withdrawn": True})

        cli(["offline", "receive", "--db", off, "--file", file_b2])
        c_rev = cli(["offline", "control", "--db", off,
                     "--artifact", str(art_a), "--artifact", str(art_b2),
                     "--shared", "%d:u=%d:v:+1" % (art_a, art_b2),
                     "--absolute", "%d:v:+1" % art_b2])["control"]
        r_rev = cli(["offline", "report", "--db", off, "--control", str(c_rev),
                     "--select", "%d:p" % art_a, "--select", "%d:q" % art_b2])
        steps.append({"revised_B_signed": {"control": c_rev, "report": r_rev["report_number"],
                                            "state": r_rev["state"], "net": r_rev["net"]}})

        # --- re-claim old and new reports by number --------------------
        old = cli(["offline", "report-get", "--db", off, "--report", str(r_same["report_number"])])
        new = cli(["offline", "report-get", "--db", off, "--report", str(r_rev["report_number"])])
        steps.append({"reclaim": {
            "old_report": {"number": old["report_number"], "net": old["net"]},
            "new_report": {"number": new["report_number"], "net": new["net"]},
            "old_digest_stable": old["digest"] == r_same["digest"],
        }})

        print(json.dumps({"kind": "pointwise-demo", "steps": steps},
                         ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
