import os
from pathlib import Path
import tempfile
import unittest

from blueprint.core import (build_topology, draft_blueprint, parse_repository)

MAKEFILE = """\
.PHONY: all
all: build test

build: deps
\tgcc -o app main.c

test: build
\t./run_tests.sh

deps:
\t./fetch_deps.sh

VER := $(shell git describe)
package: build
\ttar czf app.tgz app
"""

PACKAGE_JSON = """{
  "name": "demo",
  "scripts": {"build": "tsc", "test": "jest", "deploy": "echo push"}
}"""

PYPROJECT = """\
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "demo"
version = "0.1"
"""

WORKFLOW = """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make build
  test:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - run: make test
  release:
    needs: [test]
    environment: production
    runs-on: ubuntu-latest
    steps:
      - run: make package
"""


def make_repo():
    root = tempfile.mkdtemp()
    Path(os.path.join(root, "Makefile")).write_text(MAKEFILE, encoding="utf-8")
    Path(os.path.join(root, "package.json")).write_text(PACKAGE_JSON, encoding="utf-8")
    Path(os.path.join(root, "pyproject.toml")).write_text(PYPROJECT, encoding="utf-8")
    wf = os.path.join(root, ".github", "workflows")
    os.makedirs(wf)
    Path(os.path.join(wf, "ci.yml")).write_text(WORKFLOW, encoding="utf-8")
    return root


class Parsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = make_repo()
        cls.canon = parse_repository(cls.repo)
        cls.by_name = {t["name"]: t for t in cls.canon["targets"]}

    def test_discovery_inventory(self):
        self.assertEqual(self.canon["discovered"],
                         ["Makefile", "package.json", "pyproject.toml",
                          ".github/workflows/ci.yml"])

    def test_make_targets_with_deps_and_commands(self):
        t = self.by_name["build"]
        self.assertEqual(t["kind"], "build")
        self.assertEqual(t["depends_on"], ["deps"])
        self.assertIn("gcc", t["command"])
        self.assertEqual(self.by_name["package"]["kind"], "package")

    def test_npm_and_python_targets(self):
        self.assertEqual(self.by_name["npm:test"]["kind"], "test")
        self.assertEqual(self.by_name["npm:deploy"]["kind"], "deploy")
        self.assertIn("setuptools.build_meta",
                      self.by_name["python-build"]["command"])

    def test_workflow_jobs_needs_and_gate(self):
        rel = self.by_name["ci:ci.yml:release"]
        self.assertEqual(rel["depends_on"], ["ci:ci.yml:test"])
        self.assertEqual(rel["gate"], {"environment": "production"})
        self.assertIn("actions/checkout@v4",
                      self.by_name["ci:ci.yml:build"]["actions_used"])

    def test_provenance_on_every_target(self):
        for t in self.canon["targets"]:
            self.assertTrue(t["provenance"]["source_digest"].startswith("sha256:"))
            self.assertTrue(os.path.isabs(t["provenance"]["source_path"]))

    def test_dynamic_construct_flagged_not_guessed(self):
        flags = self.canon["flagged_constructs"]
        self.assertTrue(any("$(shell" in f["construct"] for f in flags))


class MalformedFixtures(unittest.TestCase):
    def test_malformed_json_and_yaml_flagged(self):
        root = tempfile.mkdtemp()
        Path(os.path.join(root, "package.json")).write_text("{not json", encoding="utf-8")
        wf = os.path.join(root, ".github", "workflows")
        os.makedirs(wf)
        Path(os.path.join(wf, "bad.yml")).write_text("jobs: [::bad", encoding="utf-8")
        Path(os.path.join(wf, "not_wf.yml")).write_text("name: x\n", encoding="utf-8")
        canon = parse_repository(root)
        reasons = " | ".join(f["reason"] for f in canon["flagged_constructs"])
        self.assertIn("malformed JSON", reasons)
        self.assertIn("malformed YAML", reasons)
        self.assertIn("not a workflow", reasons)
        self.assertEqual(canon["targets"], [])


class Topology(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canon = parse_repository(make_repo())
        cls.topo = build_topology(cls.canon)

    def test_stages(self):
        self.assertIn("ci:ci.yml:build", self.topo["stages"]["build"])
        self.assertIn("ci:ci.yml:test", self.topo["stages"]["test"])
        self.assertIn("package", self.topo["stages"]["package"])

    def test_edges_resolved(self):
        self.assertIn({"from": "ci:ci.yml:test", "to": "ci:ci.yml:release"},
                      self.topo["edges"])
        self.assertIn({"from": "build", "to": "test"}, self.topo["edges"])

    def test_gate_recorded_with_external_approval_note(self):
        g = self.topo["gates"][0]
        self.assertEqual(g["job"], "ci:ci.yml:release")
        self.assertIn("human approval remains external", g["note"])


class Draft(unittest.TestCase):
    def test_draft_boundary_structural(self):
        canon = parse_repository(make_repo())
        bp = draft_blueprint(canon, build_topology(canon))
        self.assertTrue(bp["draft"])
        self.assertTrue(bp["human_review_required"])
        self.assertIn("cannot deploy", bp["read_only_boundary"])
        self.assertIn("open_questions", bp)
        import blueprint.core as m
        for name in dir(m):
            for bad in ("deploy_", "apply", "merge", "approve", "provision"):
                self.assertNotIn(bad, name.lower())

    def test_traceability_complete(self):
        canon = parse_repository(make_repo())
        bp = draft_blueprint(canon, build_topology(canon))
        self.assertEqual(set(bp["traceability"]),
                         {t["name"] for t in canon["targets"]})

    def test_deterministic(self):
        repo = make_repo()
        a = draft_blueprint(parse_repository(repo),
                            build_topology(parse_repository(repo)))
        b = draft_blueprint(parse_repository(repo),
                            build_topology(parse_repository(repo)))
        self.assertEqual(a, b)


class Hardening(unittest.TestCase):
    """Regression tests for A008 findings (v0.1.1-partial)."""

    def _repo(self, **files):
        root = tempfile.mkdtemp()
        for rel, content in files.items():
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p) or root, exist_ok=True)
            Path(p).write_text(content, encoding="utf-8")
        return root

    # A008-F1: outputs must not alias the canonical model
    def test_blueprint_isolated_from_canonical(self):
        import copy
        canon = parse_repository(make_repo())
        snapshot = copy.deepcopy(canon)
        topo = build_topology(canon)
        bp = draft_blueprint(canon, topo)
        for prov in bp["traceability"].values():
            prov["source_digest"] = "TAMPERED"
        for g in bp["pipeline"]["gates"]:
            g["gate"]["environment"] = "TAMPERED"
        bp["open_questions"].append({"injected": True})
        for e in bp["pipeline"]["edges"]:
            e["from"] = "TAMPERED"
        self.assertEqual(canon, snapshot)
        # topology gates also isolated from canonical
        topo["gates"][0]["gate"]["environment"] = "TAMPERED2"
        self.assertEqual(canon, snapshot)

    # A008-F2: structurally invalid package.json is flagged, not crashed on
    def test_package_json_non_object_flagged(self):
        canon = parse_repository(self._repo(**{"package.json": "[1, 2]"}))
        self.assertEqual(canon["targets"], [])
        self.assertTrue(any("not a JSON object" in f["reason"]
                            for f in canon["flagged_constructs"]))

    def test_package_json_scripts_non_object_flagged(self):
        canon = parse_repository(
            self._repo(**{"package.json": '{"scripts": "build"}'}))
        self.assertEqual(canon["targets"], [])
        self.assertTrue(any("scripts is not an object" in f["reason"]
                            for f in canon["flagged_constructs"]))

    def test_package_json_valid_still_parsed(self):
        canon = parse_repository(
            self._repo(**{"package.json": '{"scripts": {"test": "jest"}}'}))
        self.assertEqual(canon["targets"][0]["name"], "npm:test")

    # A008-F3: structurally invalid workflow shapes are flagged, not crashed on
    def test_workflow_jobs_non_mapping_flagged(self):
        canon = parse_repository(
            self._repo(**{".github/workflows/a.yml": "jobs:\n - a\n"}))
        self.assertEqual(canon["targets"], [])
        self.assertTrue(any("jobs section is not a mapping" in f["reason"]
                            for f in canon["flagged_constructs"]))

    def test_workflow_steps_non_list_flagged(self):
        wf = "jobs:\n  good:\n    steps:\n      - run: ok\n  bad:\n    steps: 5\n"
        canon = parse_repository(self._repo(**{".github/workflows/a.yml": wf}))
        names = [t["name"] for t in canon["targets"]]
        self.assertEqual(names, ["ci:a.yml:good"])
        self.assertTrue(any(f["construct"] == "jobs.bad.steps"
                            for f in canon["flagged_constructs"]))

    # A008-F4: clear error contract for a bad repository root
    def test_missing_root_raises_valueerror(self):
        with self.assertRaises(ValueError):
            parse_repository("/nonexistent-dir-a008-probe")

    # A008-F5: duplicate Makefile targets are flagged as ambiguous
    def test_duplicate_make_targets_flagged(self):
        mk = "build:\n\techo a\nbuild:\n\techo b\n"
        canon = parse_repository(self._repo(Makefile=mk))
        self.assertTrue(any("duplicate target" in f["reason"]
                            for f in canon["flagged_constructs"]))

    def test_unique_make_targets_not_flagged(self):
        mk = "build:\n\techo a\ntest: build\n\techo b\n"
        canon = parse_repository(self._repo(Makefile=mk))
        self.assertFalse(any("duplicate target" in f["reason"]
                             for f in canon["flagged_constructs"]))


if __name__ == "__main__":
    unittest.main()
