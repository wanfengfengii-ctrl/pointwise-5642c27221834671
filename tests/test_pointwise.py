"""End-to-end tests driving the CLI the way the run manifest does."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(args, expect=0):
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "pointwise"] + args,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != expect:
        raise AssertionError(
            "expected exit %d, got %d\nargs=%r\nstdout=%s\nstderr=%s"
            % (expect, proc.returncode, args, proc.stdout, proc.stderr)
        )
    out = json.loads(proc.stdout) if proc.stdout.strip() else None
    err = json.loads(proc.stderr) if proc.stderr.strip() else None
    return out, err


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.src = os.path.join(self.dir, "src.db")
        self.off = os.path.join(self.dir, "off.db")

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.dir, name)

    # -- convenience builders -------------------------------------------
    def import_sheet(self, sheet, unit, measures, edges=()):
        args = ["source", "import", "--db", self.src, "--sheet", sheet, "--unit", unit]
        for m in measures:
            args += ["--measure", m]
        for e in edges:
            args += ["--edge", e]
        out, _ = run(args)
        return out["revision"]

    def issue(self, revision, items=(), terminals=()):
        args = ["source", "issue", "--db", self.src, "--revision", str(revision)]
        for i in items:
            args += ["--item", i]
        for t in terminals:
            args += ["--terminal", t]
        out, _ = run(args)
        return out["artifact"]

    def pull(self, artifact, name):
        target = self.path(name)
        run(["source", "pull", "--db", self.src, "--artifact", str(artifact), "--out", target])
        return target

    def receive(self, path):
        out, _ = run(["offline", "receive", "--db", self.off, "--file", path])
        return out["artifact"]

    def control(self, artifacts, shared=(), absolutes=()):
        args = ["offline", "control", "--db", self.off]
        for a in artifacts:
            args += ["--artifact", str(a)]
        for s in shared:
            args += ["--shared", s]
        for ab in absolutes:
            args += ["--absolute", ab]
        out, _ = run(args)
        return out["control"]

    def report(self, control, selections):
        args = ["offline", "report", "--db", self.off, "--control", str(control)]
        for s in selections:
            args += ["--select", s]
        out, _ = run(args)
        return out


class SourceTests(Harness):
    def test_revision_is_immutable_and_incrementing(self):
        r1 = self.import_sheet("A", "微米", ["b1:u:7"])
        r2 = self.import_sheet("A", "微米", ["b1:u:8"])
        self.assertEqual((r1, r2), (1, 2))
        first = run(["source", "revision", "--db", self.src, "--revision", "1"])[0]
        self.assertEqual(first["measures"][0]["reading"], 7)

    def test_non_micrometre_refused(self):
        _, err = run(
            ["source", "import", "--db", self.src, "--sheet", "A", "--unit", "mm",
             "--measure", "b1:u:7"],
            expect=2,
        )
        self.assertEqual(err["code"], "refused")

    def test_duplicate_original_refused(self):
        _, err = run(
            ["source", "import", "--db", self.src, "--sheet", "A", "--unit", "微米",
             "--measure", "b1:u:7", "--measure", "b1:v:7"],
            expect=2,
        )
        self.assertEqual(err["code"], "refused")

    def test_source_contradiction_refused(self):
        _, err = run(
            ["source", "import", "--db", self.src, "--sheet", "A", "--unit", "微米",
             "--measure", "b1:a:7",
             "--edge", "a:b:+1", "--edge", "b:c:+1", "--edge", "a:c:-1"],
            expect=4,
        )
        self.assertEqual(err["code"], "conflict")

    def test_unknown_revision_reports_missing(self):
        _, err = run(
            ["source", "issue", "--db", self.src, "--revision", "9", "--item", "p:b1"],
            expect=3,
        )
        self.assertEqual(err["code"], "missing")

    def test_unknown_mapping_target_reports_missing(self):
        self.import_sheet("A", "微米", ["b1:u:7"])
        _, err = run(
            ["source", "issue", "--db", self.src, "--revision", "1",
             "--item", "p:nope"],
            expect=3,
        )
        self.assertEqual(err["code"], "missing")


class ArtifactTests(Harness):
    def test_artifact_hides_internal_reference_and_orig(self):
        rev = self.import_sheet("A", "微米", ["b1:internal_ref:7"],
                                ["u:internal_ref:+1"])
        art = self.issue(rev, items=["p:b1"], terminals=["u:internal_ref"])
        doc = run(["source", "pull", "--db", self.src, "--artifact", str(art)])[0]
        text = json.dumps(doc, ensure_ascii=False)
        self.assertNotIn("internal_ref", text)
        self.assertNotIn("b1", text)
        self.assertEqual(doc["content"]["items"], [{"name": "p", "reading": 7}])

    def test_artifact_preserves_derivable_relations(self):
        # public u --same--> r (private) --reverse--> p; p is the item's own ref
        rev = self.import_sheet("A", "微米", ["b1:p:7"],
                                ["u:r:+1", "p:r:-1"])
        art = self.issue(rev, items=["p:b1"], terminals=["u:u", "r2:r"])
        doc = run(["source", "pull", "--db", self.src, "--artifact", str(art)])[0]
        edges = {tuple(sorted((e["a"]["name"], e["b"]["name"]))): e["sign"]
                 for e in doc["content"]["edges"]}
        # u and r2 same; r2 and p reverse; u and p reverse
        self.assertEqual(edges[("r2", "u")], 1)
        self.assertEqual(edges[("p", "r2")], -1)
        self.assertEqual(edges[("p", "u")], -1)

    def test_artifact_reclaim_by_number_is_identical(self):
        rev = self.import_sheet("A", "微米", ["b1:u:7"])
        art = self.issue(rev, items=["p:b1"])
        again = self.issue(rev, items=["p:b1"])
        self.assertEqual(art, again)
        a = run(["source", "pull", "--db", self.src, "--artifact", str(art)])[0]
        b = run(["source", "pull", "--db", self.src, "--artifact", str(art)])[0]
        self.assertEqual(a, b)


class OfflineRefusalTests(Harness):
    def _one(self):
        rev = self.import_sheet("A", "微米", ["b1:u:7"])
        art = self.issue(rev, items=["p:b1"], terminals=["u:u"])
        return self.receive(self.pull(art, "a.json"))

    def test_bad_file_refused(self):
        self._one()
        with open(self.path("a.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["content"]["items"][0]["reading"] = 9
        with open(self.path("bad.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        _, err = run(["offline", "receive", "--db", self.off,
                      "--file", self.path("bad.json")], expect=5)
        self.assertEqual(err["code"], "corrupt")

    def test_same_number_different_artifact_refused(self):
        n = self._one()
        with open(self.path("a.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["content"]["items"][0]["reading"] = 3
        sys.path.insert(0, ROOT)
        from pointwise.model import digest_of
        doc["content_digest"] = digest_of(doc["content"])
        with open(self.path("same.json"), "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        _, err = run(["offline", "receive", "--db", self.off,
                      "--file", self.path("same.json")], expect=6)
        self.assertEqual(err["code"], "duplicate")

    def test_unlisted_terminal_refused(self):
        n = self._one()
        _, err = run(["offline", "control", "--db", self.off, "--artifact", str(n),
                      "--shared", "%d:zz=%d:u:+1" % (n, n)], expect=2)
        self.assertEqual(err["code"], "refused")

    def test_unknown_report_missing(self):
        _, err = run(["offline", "report-get", "--db", self.off, "--report", "5"],
                     expect=3)
        self.assertEqual(err["code"], "missing")


class ReportScenarioTests(Harness):
    def setUp(self):
        super().setUp()
        # A: u --same--> r --reverse--> p   (private reference r, item p on ref p)
        ra = self.import_sheet("A", "微米", ["b1:p:7"], ["u:r:+1", "p:r:-1"])
        self.a = self.issue(ra, items=["p:b1"], terminals=["u:u"])
        self.pa = self.pull(self.a, "a.json")
        self.receive(self.pa)
        # B: v --same--> q
        rb = self.import_sheet("B", "微米", ["b2:q:7"], ["v:q:+1"])
        self.b = self.issue(rb, items=["q:b2"], terminals=["v:v"])
        self.pb = self.pull(self.b, "b.json")
        self.receive(self.pb)

    def test_same_direction_shared_signs_zero(self):
        c = self.control([self.a, self.b], shared=["%d:u=%d:v:+1" % (self.a, self.b)])
        r = self.report(c, ["%d:p" % self.a, "%d:q" % self.b])
        self.assertEqual(r["state"], "signed")
        self.assertEqual(r["net"], 0)

    def test_no_shared_edge_undetermined(self):
        c = self.control([self.a, self.b])
        r = self.report(c, ["%d:p" % self.a, "%d:q" % self.b])
        self.assertEqual(r["state"], "undetermined")
        self.assertIsNone(r["net"])

    def test_same_and_reverse_coexist_contradiction(self):
        c = self.control(
            [self.a, self.b],
            shared=["%d:u=%d:v:+1" % (self.a, self.b),
                    "%d:u=%d:v:-1" % (self.a, self.b)],
        )
        r = self.report(c, ["%d:p" % self.a, "%d:q" % self.b])
        self.assertEqual(r["state"], "contradiction")
        self.assertIsNone(r["net"])

    def test_revised_sheet_signed_minus_two_and_old_report_kept(self):
        c_old = self.control([self.a, self.b], shared=["%d:u=%d:v:+1" % (self.a, self.b)])
        old = self.report(c_old, ["%d:p" % self.a, "%d:q" % self.b])
        self.assertEqual(old["net"], 0)

        # Revise B: reading 5, keep same-direction shared edge, anchor v positive.
        rb2 = self.import_sheet("B", "微米", ["b3:q:5"], ["v:q:+1"])
        b2 = self.issue(rb2, items=["q:b3"], terminals=["v:v"])
        self.receive(self.pull(b2, "b2.json"))
        c_new = self.control(
            [self.a, b2],
            shared=["%d:u=%d:v:+1" % (self.a, b2)],
            absolutes=["%d:v:+1" % (b2,)],
        )
        new = self.report(c_new, ["%d:p" % self.a, "%d:q" % b2])
        self.assertEqual(new["state"], "signed")
        self.assertEqual(new["net"], -2)

        # Old report still claims its original value; new report reopens the same.
        again = run(["offline", "report-get", "--db", self.off,
                     "--report", str(old["report_number"])])[0]
        self.assertEqual(again["net"], 0)
        self.assertEqual(again["digest"], old["digest"])
        reopened = run(["offline", "report-get", "--db", self.off,
                        "--report", str(new["report_number"])])[0]
        self.assertEqual(reopened["net"], -2)
        self.assertEqual(reopened["digest"], new["digest"])

    def test_contradiction_in_untouched_component_does_not_block_signing(self):
        # A/B share same-direction (net 0). C/D share same+reverse (contradiction),
        # but we only select p and q, whose component is clean.
        rc = self.import_sheet("C", "微米", ["b9:c:3"], ["w:c:+1"])
        art_c = self.issue(rc, items=["c:b9"], terminals=["w:w"])
        self.receive(self.pull(art_c, "c.json"))
        rd = self.import_sheet("D", "微米", ["b8:d:3"], ["x:d:+1"])
        art_d = self.issue(rd, items=["d:b8"], terminals=["x:x"])
        self.receive(self.pull(art_d, "d.json"))
        c = self.control(
            [self.a, self.b, art_c, art_d],
            shared=["%d:u=%d:v:+1" % (self.a, self.b),
                    "%d:w=%d:x:+1" % (art_c, art_d),
                    "%d:w=%d:x:-1" % (art_c, art_d)],
        )
        r = self.report(c, ["%d:p" % self.a, "%d:q" % self.b])
        self.assertEqual(r["state"], "signed")
        self.assertEqual(r["net"], 0)

    def test_report_stores_parents_control_and_selection(self):
        c = self.control([self.a, self.b], shared=["%d:u=%d:v:+1" % (self.a, self.b)])
        r = self.report(c, ["%d:p" % self.a, "%d:q" % self.b])
        self.assertEqual(r["parents"], sorted([self.a, self.b]))
        self.assertEqual(r["control"], c)
        self.assertEqual(
            r["selections"],
            [{"artifact": self.a, "item": "p"}, {"artifact": self.b, "item": "q"}],
        )

    def test_absolute_sign_and_relative_reading(self):
        # Anchor u negative: p = -u -> positive; q = v = u -> negative.
        c = self.control(
            [self.a, self.b],
            shared=["%d:u=%d:v:+1" % (self.a, self.b)],
            absolutes=["%d:u:-1" % self.a],
        )
        r = self.report(c, ["%d:p" % self.a, "%d:q" % self.b])
        # p: reading 7 * (+1) = 7 ; q: reading 7 * (-1) = -7 ; net 0
        self.assertEqual(r["state"], "signed")
        self.assertEqual(r["net"], 0)

    def test_two_independent_grounded_components_sum(self):
        rc = self.import_sheet("C", "微米", ["b9:c:3"], ["w:c:+1"])
        art_c = self.issue(rc, items=["c:b9"], terminals=["w:w"])
        self.receive(self.pull(art_c, "c.json"))
        c = self.control(
            [self.a, art_c],
            absolutes=["%d:u:+1" % self.a, "%d:w:+1" % art_c],
        )
        # a: u same p? A is u --+1-- r --(-1)-- p, so p = -u -> -7 with u=+1.
        # c: w same c (+1) -> +3. Two separate grounded components -> -4.
        r = self.report(c, ["%d:p" % self.a, "%d:c" % art_c])
        self.assertEqual(r["state"], "signed")
        self.assertEqual(r["net"], -4)


class ReportRefusalTests(Harness):
    def setUp(self):
        super().setUp()
        ra = self.import_sheet("A", "微米", ["b1:u:7"], ["u:p:+1"])
        self.a = self.issue(ra, items=["p:b1"], terminals=["u:u"])
        self.receive(self.pull(self.a, "a.json"))
        self.c = self.control([self.a])

    def test_empty_selection_refused(self):
        _, err = run(["offline", "report", "--db", self.off,
                      "--control", str(self.c)], expect=2)
        self.assertEqual(err["code"], "refused")

    def test_duplicate_selection_refused(self):
        _, err = run(["offline", "report", "--db", self.off, "--control", str(self.c),
                      "--select", "%d:p" % self.a, "--select", "%d:p" % self.a],
                     expect=2)
        self.assertEqual(err["code"], "refused")

    def test_item_not_on_parent_refused(self):
        _, err = run(["offline", "report", "--db", self.off, "--control", str(self.c),
                      "--select", "%d:q" % self.a], expect=2)
        self.assertEqual(err["code"], "refused")

    def test_artifact_not_in_control_refused(self):
        _, err = run(["offline", "report", "--db", self.off, "--control", str(self.c),
                      "--select", "999:p"], expect=2)
        self.assertEqual(err["code"], "refused")

    def test_refusal_leaves_no_report(self):
        run(["offline", "report", "--db", self.off, "--control", str(self.c)], expect=2)
        out = run(["offline", "reports", "--db", self.off])[0]
        self.assertEqual(out["reports"], [])


if __name__ == "__main__":
    unittest.main()
