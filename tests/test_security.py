import copy
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from blueprint.core import parse_repository, build_topology, draft_blueprint

class SecurityRegressions(unittest.TestCase):
    def repo(self, files):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for name, value in files.items():
            path = Path(temp.name) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(value, bytes):
                path.write_bytes(value)
            else:
                path.write_text(value, encoding="utf-8")
        return temp.name

    def parse(self, files):
        return parse_repository(self.repo(files))

    def workflow(self, body):
        return self.parse({".github/workflows/ci.yml": body})

    def test_workflows_have_distinct_job_identity(self):
        body = "jobs:\n  build:\n    steps:\n      - run: test\n"
        c = self.parse({".github/workflows/a.yml": body, ".github/workflows/b.yml": body})
        self.assertEqual({t["name"] for t in c["targets"]}, {"ci:a.yml:build", "ci:b.yml:build"})
        self.assertEqual(len(draft_blueprint(c, build_topology(c))["traceability"]), 2)

    def test_dependencies_never_cross_workflow(self):
        c = self.parse({".github/workflows/a.yml": "jobs:\n  build:\n    steps:\n      - run: test\n",
                        ".github/workflows/b.yml": "jobs:\n  test:\n    needs: build\n    steps:\n      - run: test\n"})
        t = build_topology(c)
        self.assertEqual(t["edges"], [])
        self.assertEqual(t["unresolved_dependencies"][0]["depends_on"], "ci:b.yml:build")

    def test_duplicate_json_keys_rejected(self):
        c = self.parse({"package.json": '{"scripts":{"test":"one","test":"two"}}'})
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_duplicate_yaml_keys_rejected(self):
        c = self.workflow("jobs:\n  test:\n    steps: []\n  test:\n    steps: []\n")
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_yaml_aliases_rejected(self):
        c = self.workflow("jobs:\n  a: &shared\n    steps: []\n  b: *shared\n")
        self.assertEqual(c["targets"], [])

    def test_recursive_yaml_rejected(self):
        c = self.workflow("jobs: &a {test: *a}")
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_yaml_object_tags_rejected(self):
        c = self.workflow("jobs: !!python/object/apply:os.system ['unsafe']")
        self.assertEqual(c["targets"], [])

    def test_yaml_12_on_job_kept_as_string(self):
        c = self.workflow("on: [push]\njobs:\n  on:\n    steps:\n      - run: echo ok\n")
        self.assertEqual(c["targets"][0]["name"], "ci:ci.yml:on")

    def test_boolean_job_identifier_rejected(self):
        c = self.workflow("jobs:\n  true:\n    steps: []\n")
        self.assertEqual(c["targets"], [])

    def test_invalid_run_field_flagged(self):
        c = self.workflow("jobs:\n  test:\n    steps:\n      - run: [one, two]\n")
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_invalid_step_shapes_flagged(self):
        for step in ("42", "{run: hi, uses: action}", "{name: nothing}"):
            with self.subTest(step=step):
                c = self.workflow("jobs:\n  test:\n    steps:\n      - "+step+"\n")
                self.assertEqual(c["targets"], [])

    def test_invalid_needs_shapes_flagged(self):
        for needs in ("true", "123", "{a: b}", "[true]"):
            with self.subTest(needs=needs):
                c = self.workflow("jobs:\n  test:\n    needs: "+needs+"\n    steps: []\n")
                self.assertEqual(c["targets"], [])

    def test_invalid_environment_flagged(self):
        c = self.workflow("jobs:\n  test:\n    environment: 42\n    steps: []\n")
        self.assertEqual(c["targets"], [])

    def test_job_context_is_preserved_and_flagged(self):
        c = self.workflow("jobs:\n  test:\n    if: success()\n    steps:\n      - run: echo hi\n        shell: bash\n")
        t = c["targets"][0]
        self.assertEqual(t["definition"]["steps"][0]["shell"], "bash")
        self.assertTrue(c["flagged_constructs"])

    def test_reusable_workflow_recorded_not_expanded(self):
        c = self.workflow("jobs:\n  test:\n    uses: org/repo/.github/workflows/test.yml@v1\n")
        self.assertEqual(c["targets"][0]["actions_used"], ["org/repo/.github/workflows/test.yml@v1"])
        self.assertTrue(c["flagged_constructs"])

    def test_nonfinite_yaml_rejected(self):
        c = self.workflow("jobs:\n  test:\n    env: {VALUE: .nan}\n    steps: []\n")
        self.assertEqual(c["targets"], [])

    def test_invalid_npm_script_type_flagged(self):
        c = self.parse({"package.json": '{"scripts":{"test":true,"build":"tsc"}}'})
        self.assertEqual([t["name"] for t in c["targets"]], ["npm:build"])
        self.assertTrue(c["flagged_constructs"])

    def test_null_scripts_flagged(self):
        c = self.parse({"package.json": '{"scripts":null}'})
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_toml_invalid_tables_flagged(self):
        for value in ('build-system = "x"', 'project = "x"', '[project]\nscripts = 3'):
            with self.subTest(value=value):
                c = self.parse({"pyproject.toml": value})
                self.assertEqual(c["targets"], [])
                self.assertTrue(c["flagged_constructs"])

    def test_python_entrypoint_reference_preserved(self):
        c = self.parse({"pyproject.toml": '[project.scripts]\nhello = "pkg.cli:main"'})
        self.assertEqual(c["targets"][0]["entrypoint"], "pkg.cli:main")

    def test_make_unsupported_rules_are_not_misparsed(self):
        for rule in ("build: ; echo secret", "build: x | y", "build:: other", "build: X=one"):
            with self.subTest(rule=rule):
                c = self.parse({"Makefile": rule+"\n"})
                self.assertEqual(c["targets"], [])
                self.assertTrue(c["flagged_constructs"])

    def test_make_recipe_lines_remain_separate(self):
        c = self.parse({"Makefile": "build:\n\tfalse\n\techo next\n"})
        self.assertEqual(c["targets"][0]["commands"], ["false", "echo next"])
        self.assertNotIn(" && ", c["targets"][0]["command"])

    def test_dynamic_recipe_is_flagged_without_execution(self):
        c = self.parse({"Makefile": "build:\n\techo $(shell credential-example)\n"})
        self.assertTrue(c["flagged_constructs"])
        self.assertNotIn("credential-example", json.dumps(c["flagged_constructs"]))

    def test_parser_errors_do_not_leak_source_text(self):
        c = self.workflow("jobs: [credential-example: {")
        self.assertNotIn("credential-example", json.dumps(c["flagged_constructs"]))

    def test_invalid_utf8_flagged(self):
        c = self.parse({"Makefile": b"build:\n\t\xff"})
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_nonregular_definition_flagged(self):
        root = self.repo({})
        (Path(root)/"Makefile").mkdir()
        c = parse_repository(root)
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_file_byte_limit(self):
        root = self.repo({"Makefile": "build:\n\techo hello\n"})
        with patch("blueprint.core.MAX_FILE_BYTES", 8):
            c = parse_repository(root)
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_aggregate_byte_limit(self):
        root = self.repo({"Makefile": "build:\n", "package.json": '{"scripts":{"test":"ok"}}'})
        with patch("blueprint.core.MAX_TOTAL_BYTES", 10):
            c = parse_repository(root)
        self.assertEqual(len(c["targets"]), 1)
        self.assertTrue(c["flagged_constructs"])

    def test_workflow_file_limit(self):
        root = self.repo({".github/workflows/a.yml": "jobs: {}", ".github/workflows/b.yml": "jobs: {}"})
        with patch("blueprint.core.MAX_FILES", 1):
            c = parse_repository(root)
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_target_limit(self):
        root = self.repo({"Makefile": "build:\ntest:\n"})
        with patch("blueprint.core.MAX_TARGETS", 1):
            c = parse_repository(root)
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_linked_source_is_rejected_before_open(self):
        root = self.repo({"Makefile": "build:\n"})
        original = Path.lstat
        def linked(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path.name == "Makefile":
                values = list(result)
                values[0] = stat.S_IFLNK | 0o777
                return os.stat_result(values)
            return result
        with patch("blueprint.core.Path.lstat", linked):
            c = parse_repository(root)
        self.assertEqual(c["targets"], [])
        self.assertTrue(c["flagged_constructs"])

    def test_duplicate_target_topology_rejected(self):
        c = self.parse({"Makefile": "build:\n\tone\nbuild:\n\ttwo\n"})
        with self.assertRaises(ValueError):
            build_topology(c)

    def test_cycles_and_downstream_nodes_are_open_questions(self):
        c = self.parse({"Makefile": "a: b\nb: a\nc: b\n"})
        t = build_topology(c)
        self.assertEqual({x["job"] for x in t["cycle_questions"]}, {"a", "b", "c"})
        self.assertTrue(draft_blueprint(c, t)["open_questions"])

    def test_self_cycle_detected(self):
        c = self.parse({"Makefile": "a: a\n"})
        self.assertEqual(build_topology(c)["cycle_questions"][0]["job"], "a")

    def test_forged_topology_rejected(self):
        c = self.parse({"Makefile": "build:\n"})
        t = build_topology(c)
        t["stages"]["deploy"] = ["unreviewed"]
        with self.assertRaises(ValueError):
            draft_blueprint(c, t)

    def test_stale_topology_rejected(self):
        c = self.parse({"Makefile": "build:\n\techo old\n"})
        t = build_topology(c)
        c["targets"][0]["command"] = "echo new"
        with self.assertRaises(ValueError):
            draft_blueprint(c, t)

    def test_invalid_canonical_fields_rejected(self):
        c = self.parse({"Makefile": "build:\n"})
        for changes in ({"kind": "unknown"}, {"depends_on": "build"}, {"provenance": {}}):
            bad = copy.deepcopy(c)
            bad["targets"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_topology(bad)

