"""CI/CD & IaC Blueprint Builder — deterministic core (JY-S007-P001).

PAPER-CAP-01 slice: parse repository build/test/packaging/deployment
definitions (Makefile, package.json, pyproject.toml, GitHub Actions
workflow YAML) into a canonical artifact model, preserving source file
path and content digest for every extracted target and flagging
unsupported or ambiguous constructs instead of guessing.
PAPER-CAP-02 slice: construct a stages/jobs/gates/dependencies topology
from the canonical model and emit a reviewable draft blueprint.

Governing boundary, enforced structurally: read-only against
infrastructure — the builder reads files it is pointed at and returns
draft artifacts; no deploy/apply/merge/approve API exists, and every
output is marked `draft: true` with `human_review_required: true`.
Model output is never approval.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from collections import deque
import tomli

import yaml

VERSION = "0.1.2a1"
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILES = 256
MAX_TARGETS = 10000

def _text(value):
    return (type(value) is str and 0 < len(value) <= 512
            and not any(ord(c) < 32 for c in value))

def _read(path, rep):
    try:
        candidate = Path(path).absolute()
        boundary = Path(rep.root) if rep.root else candidate.parent
        relative = candidate.relative_to(boundary)
        current = boundary
        for part in relative.parts:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("linked paths are not read")
        if not stat.S_ISREG(candidate.stat().st_mode):
            raise ValueError("source is not a regular file")
        with candidate.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES or rep.bytes_read + len(data) > MAX_TOTAL_BYTES:
            raise ValueError("source size limit exceeded")
        rep.bytes_read += len(data)
        data.decode("utf-8")
        return data
    except (OSError, ValueError, UnicodeError):
        rep.flag(str(path), "source", "source unreadable, linked, non-UTF-8, or over size limit")
        return None

def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result

class _WorkflowLoader(yaml.SafeLoader):
    # GitHub workflows use YAML 1.2 boolean spellings; keep on/off as strings.
    yaml_implicit_resolvers = {
        key: [(tag, pattern) for tag, pattern in values
              if tag != "tag:yaml.org,2002:bool"]
        for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

_WorkflowLoader.add_implicit_resolver("tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))

def _mapping(loader, node):
    pairs = []
    for key, value in node.value:
        parsed_key = loader.construct_object(key, deep=True)
        if type(parsed_key) is not str:
            raise ValueError("mapping keys must be strings")
        pairs.append((parsed_key, loader.construct_object(value, deep=True)))
    return _unique_pairs(pairs)

_WorkflowLoader.add_constructor("tag:yaml.org,2002:map", _mapping)

def _workflow_data(data):
    depth = 0
    for count, token in enumerate(yaml.scan(data)):
        if count > 20000:
            raise ValueError("YAML token limit")
        if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)):
            raise ValueError("YAML anchors and aliases require manual review")
        if isinstance(token, (yaml.tokens.BlockMappingStartToken, yaml.tokens.BlockSequenceStartToken,
                              yaml.tokens.FlowMappingStartToken, yaml.tokens.FlowSequenceStartToken)):
            depth += 1
            if depth > 50:
                raise ValueError("YAML nesting limit")
        elif isinstance(token, (yaml.tokens.BlockEndToken, yaml.tokens.FlowMappingEndToken,
                                yaml.tokens.FlowSequenceEndToken)):
            depth -= 1
    return yaml.load(data, Loader=_WorkflowLoader)



def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _target(name, kind, command, source_path, source_digest, **extra):
    t = {"name": name, "kind": kind, "command": command,
         "provenance": {"source_path": source_path,
                        "source_digest": source_digest}}
    t.update(extra)
    return t


class ParseReport:
    def __init__(self, root=None):
        self.root = root
        self.bytes_read = 0
        self.targets = []
        self.flags = []      # unsupported/ambiguous constructs — never guessed

    def flag(self, source_path, construct, reason):
        self.flags.append({"source_path": source_path,
                           "construct": construct, "reason": reason})


# ------------------------- parsers (CAP-01.02) ---------------------------
_MAKE_RULE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?!=)([^=].*)?$")

_KIND_HINTS = [("test", "test"), ("check", "test"), ("lint", "test"),
               ("build", "build"), ("compile", "build"),
               ("package", "package"), ("dist", "package"),
               ("deploy", "deploy"), ("release", "deploy"),
               ("publish", "deploy")]


def _kind_for(name: str, default="build"):
    low = name.lower()
    for hint, kind in _KIND_HINTS:
        if hint in low:
            return kind
    return default


def parse_makefile(path: str, rep: ParseReport) -> None:
    data = _read(path, rep)
    if data is None:
        return
    d = _digest(data)
    lines = data.decode("utf-8").splitlines()
    seen = set()
    for number, line in enumerate(lines, 1):
        if "$" in line or line.rstrip().endswith("\\") or re.match(
                r"^\s*(?:-?include|sinclude|ifeq|ifneq|ifdef|ifndef|else|endif|define|endef)\b", line):
            rep.flag(path, f"line {number}: $(shell ...) / dynamic Make syntax",
                     "dynamic or conditional Make construct not evaluated")
    i = 0
    while i < len(lines):
        m = _MAKE_RULE.match(lines[i])
        if m and not lines[i].startswith("\t"):
            name = m.group(1)
            if name.startswith("."):
                i += 1
                continue
            cmds = []
            j = i + 1
            while j < len(lines) and (lines[j].startswith("\t") or not lines[j].strip()):
                if lines[j].startswith("\t"):
                    cmds.append(lines[j].strip())
                j += 1
            declaration = (m.group(2) or "").split("#")[0]
            if any(token in declaration for token in (";", "|", ":", "$", "\\", "=", "%")):
                rep.flag(path, name, "unsupported inline, order-only, or dynamic rule; target omitted")
                i = j
                continue
            deps = declaration.split()
            if name in seen:
                rep.flag(path, name,
                         "duplicate target definition — ambiguous, later "
                         "definition recorded alongside earlier one")
            seen.add(name)
            rep.targets.append(_target(
                name, _kind_for(name), "\n".join(cmds) or None,
                path, d, depends_on=deps, commands=cmds))
            i = j
        else:
            if lines[i].strip() and not lines[i].lstrip().startswith("#") and ":" in lines[i]:
                rep.flag(path, f"line {i+1}", "unsupported Make statement not interpreted")
            i += 1


def parse_package_json(path: str, rep: ParseReport) -> None:
    data = _read(path, rep)
    if data is None:
        return
    d = _digest(data)
    try:
        pkg = json.loads(data, object_pairs_hook=_unique_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        rep.flag(path, "package.json", "malformed JSON or duplicate keys")
        return
    if not isinstance(pkg, dict):
        rep.flag(path, "package.json",
                 "top-level value is not a JSON object — not a package manifest")
        return
    scripts = pkg.get("scripts", {})
    if not isinstance(scripts, dict):
        rep.flag(path, "scripts",
                 "scripts is not an object — ambiguous, not guessed")
        return
    for name, cmd in scripts.items():
        if not _text(name) or type(cmd) is not str:
            rep.flag(path, "scripts", "script names and commands must be strings")
            continue
        rep.targets.append(_target(f"npm:{name}", _kind_for(name), cmd, path, d))


def parse_pyproject(path: str, rep: ParseReport) -> None:
    data = _read(path, rep)
    if data is None:
        return
    d = _digest(data)
    try:
        doc = tomli.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        rep.flag(path, "pyproject.toml", "malformed TOML")
        return
    if "build-system" in doc:
        build = doc["build-system"]
        if type(build) is not dict or not _text(build.get("build-backend")):
            rep.flag(path, "build-system", "missing or invalid build backend")
        else:
            rep.targets.append(_target("python-build", "package",
                f"python -m build (backend: {build['build-backend']})", path, d))
    project = doc.get("project", {})
    scripts = project.get("scripts", {}) if type(project) is dict else None
    if type(scripts) is not dict:
        rep.flag(path, "project.scripts", "project and scripts must be tables")
        return
    for name, entry in scripts.items():
        if not _text(name) or not _text(entry):
            rep.flag(path, "project.scripts", "entrypoint names and references must be strings")
            continue
        rep.targets.append(_target(f"entrypoint:{name}", "build", None, path, d,
                                   entrypoint=entry))


def parse_github_workflow(path: str, rep: ParseReport) -> None:
    data = _read(path, rep)
    if data is None:
        return
    d = _digest(data)
    try:
        wf = _workflow_data(data)
        _model_digest(wf)
    except (yaml.YAMLError, ValueError, RecursionError):
        rep.flag(path, os.path.basename(path), "malformed YAML, duplicate keys, or unsupported aliases/nesting")
        return
    if not isinstance(wf, dict) or "jobs" not in wf:
        rep.flag(path, os.path.basename(path), "no jobs section — not a workflow")
        return
    jobs = wf.get("jobs", {})
    if not isinstance(jobs, dict):
        rep.flag(path, "jobs",
                 "jobs section is not a mapping — ambiguous, not guessed")
        return
    prefix = "ci:" + os.path.basename(path) + ":"
    for job_id, job in jobs.items():
        if not _text(job_id) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", job_id):
            rep.flag(path, "jobs", "invalid job identifier")
            continue
        if type(job) is not dict:
            rep.flag(path, f"jobs.{job_id}", "ambiguous job definition")
            continue
        steps = job.get("steps", [])
        if type(steps) is not list:
            rep.flag(path, f"jobs.{job_id}.steps", "steps is not a list — ambiguous job definition")
            continue
        cmds, uses = [], []
        valid = True
        for step in steps:
            if type(step) is not dict or bool("run" in step) == bool("uses" in step):
                valid = False
                break
            field = "run" if "run" in step else "uses"
            if type(step[field]) is not str or not step[field]:
                valid = False
                break
            (cmds if field == "run" else uses).append(step[field])
        needs = job.get("needs", [])
        if type(needs) is str:
            needs = [needs]
        if type(needs) is not list or any(not _text(n) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_-]*", n) for n in needs):
            valid = False
        env_gate = job.get("environment")
        if env_gate is not None and not (_text(env_gate) or
                type(env_gate) is dict and _text(env_gate.get("name"))):
            valid = False
        if "uses" in job:
            if steps or not _text(job["uses"]):
                valid = False
            else:
                uses.append(job["uses"])
                rep.flag(path, f"jobs.{job_id}.uses", "reusable workflow not expanded")
        elif "steps" not in job:
            valid = False
        if not valid:
            rep.flag(path, f"jobs.{job_id}", "invalid job fields; target omitted")
            continue
        if any(k in job for k in ("if", "strategy", "concurrency", "continue-on-error", "defaults")) or any(
                set(step) - {"name", "run", "uses"} for step in steps):
            rep.flag(path, f"jobs.{job_id}", "conditions, matrix, defaults or step settings require manual interpretation")
        rep.targets.append(_target(
            prefix + job_id, _kind_for(job_id), "\n".join(cmds) or None, path, d,
            commands=cmds, depends_on=[prefix+n for n in dict.fromkeys(needs)],
            actions_used=uses, definition=copy.deepcopy(job),
            workflow_context=copy.deepcopy({k: v for k, v in wf.items() if k != "jobs"}),
            gate=({"environment": env_gate} if env_gate else None)))


PARSERS = {"Makefile": parse_makefile, "package.json": parse_package_json,
           "pyproject.toml": parse_pyproject}


def parse_repository(root: str) -> dict:
    """CAP-01 — discover and parse supported definition formats."""
    try:
        root = str(Path(root).resolve(strict=True))
        if not Path(root).is_dir():
            raise ValueError()
    except (OSError, TypeError, ValueError):
        raise ValueError("repository root must be an existing directory") from None
    rep = ParseReport(root)
    inventory = []
    for fname, parser in sorted(PARSERS.items()):
        candidate = Path(root) / fname
        if candidate.exists() or candidate.is_symlink():
            inventory.append(fname)
            parser(str(candidate), rep)
    wfdir = Path(root) / ".github" / "workflows"
    # Refuse linked directories before discovery to avoid reading outside the root.
    try:
        for parent in (wfdir.parent, wfdir):
            if parent.exists():
                info = parent.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("linked workflow directory")
        if wfdir.exists():
            with os.scandir(wfdir) as entries:
                names = []
                for entry in entries:
                    if entry.name.endswith((".yml", ".yaml")):
                        names.append(entry.name)
                        if len(names) > MAX_FILES:
                            raise ValueError("workflow file limit exceeded")
            for fname in sorted(names):
                inventory.append(f".github/workflows/{fname}")
                parse_github_workflow(str(wfdir / fname), rep)
    except (OSError, ValueError):
        rep.flag(str(wfdir), "workflow discovery", "workflow directory unreadable, linked, or over file limit")
    if len(rep.targets) > MAX_TARGETS:
        rep.targets = []
        rep.flag(root, "targets", "target count exceeds limit; all targets omitted")
    return {"schema": "blueprint/canonical-artifacts/v1",
            "builder_version": VERSION,
            "discovered": inventory, "targets": rep.targets,
            "flagged_constructs": rep.flags}


# --------------------- topology + blueprint (CAP-02) ----------------------
STAGE_ORDER = ["build", "test", "package", "deploy"]


def _model_digest(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("canonical model must be finite JSON") from None
    if len(encoded) > MAX_TOTAL_BYTES:
        raise ValueError("canonical model exceeds size limit")
    return _digest(encoded)

def _canonical_model(value):
    _model_digest(value)
    value = copy.deepcopy(value)
    if type(value) is not dict or type(value.get("targets")) is not list or len(value["targets"]) > MAX_TARGETS:
        raise ValueError("canonical model requires a bounded target list")
    if type(value.get("flagged_constructs")) is not list:
        raise ValueError("canonical model requires a flag list")
    names = []
    for target in value["targets"]:
        if type(target) is not dict or not _text(target.get("name")) or target.get("kind") not in STAGE_ORDER:
            raise ValueError("invalid canonical target")
        provenance = target.get("provenance")
        if type(provenance) is not dict or not _text(provenance.get("source_path")) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(provenance.get("source_digest"))):
            raise ValueError("invalid target provenance")
        dependencies = target.get("depends_on", [])
        if type(dependencies) is not list or any(not _text(n) for n in dependencies):
            raise ValueError("invalid target dependencies")
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("duplicate target dependencies")
        names.append(target["name"])
    if len(names) != len(set(names)):
        raise ValueError("duplicate target identities require manual resolution")
    return value


def build_topology(canonical: dict) -> dict:
    canonical = _canonical_model(canonical)
    stages = {s: [] for s in STAGE_ORDER}
    known = {t["name"] for t in canonical["targets"]}
    unresolved = []
    for t in canonical["targets"]:
        stages[t["kind"]].append(t["name"])
        for dep in t.get("depends_on", []):
            if dep and dep not in known:
                unresolved.append({"job": t["name"], "depends_on": dep,
                                   "note": "dependency not among parsed targets"})
    gates = [{"job": t["name"], "gate": copy.deepcopy(t["gate"]),
              "note": "environment gate — human approval remains external"}
             for t in canonical["targets"] if t.get("gate")]
    edges = [{"from": dep, "to": t["name"]}
             for t in canonical["targets"]
             for dep in t.get("depends_on", []) if dep in known]
    pending = set(known)
    indegree = dict.fromkeys(known, 0)
    outgoing = {name: [] for name in known}
    for edge in edges:
        indegree[edge["to"]] += 1
        outgoing[edge["from"]].append(edge["to"])
    ready = deque(name for name in known if indegree[name] == 0)
    while ready:
        node = ready.popleft()
        pending.remove(node)
        for neighbor in outgoing[node]:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                ready.append(neighbor)
    cycle_questions = ([{"job": name, "note": "dependency cycle or dependent of a cycle"}
                        for name in sorted(pending)])
    return {"schema": "blueprint/topology/v1",
            "canonical_digest": _model_digest(canonical),
            "cycle_questions": cycle_questions,
            "stages": {s: sorted(v) for s, v in stages.items() if v},
            "edges": sorted(edges, key=lambda e: (e["from"], e["to"])),
            "gates": gates, "unresolved_dependencies": unresolved}


def draft_blueprint(canonical: dict, topology: dict) -> dict:
    """A reviewable draft artifact — never applied by this builder."""
    canonical = _canonical_model(canonical)
    expected = build_topology(canonical)
    if topology != expected:
        raise ValueError("topology does not match the canonical model")
    topology = expected
    return {
        "schema": "blueprint/draft/v1", "builder_version": VERSION,
        "draft": True, "human_review_required": True,
        "read_only_boundary": "this builder cannot deploy, apply, merge, "
                              "auto-approve, or perform live writes",
        "pipeline": {
            "stages": [{"stage": s, "jobs": list(topology["stages"][s])}
                       for s in STAGE_ORDER if s in topology["stages"]],
            "edges": copy.deepcopy(topology["edges"]),
            "gates": copy.deepcopy(topology["gates"]),
        },
        "traceability": {t["name"]: copy.deepcopy(t["provenance"])
                         for t in canonical["targets"]},
        "open_questions": copy.deepcopy(canonical["flagged_constructs"]
                                        + topology["unresolved_dependencies"] + topology["cycle_questions"]),
    }
