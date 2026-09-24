# CI/CD Blueprint Builder

**0.1.2a1 — experimental partial candidate, JY-S007-P001**

Reads supported build and workflow definitions from a repository, records source
digests, constructs dependency topology, and returns a draft for human review.
It never executes parsed commands or writes infrastructure.

Supported discovery: root Makefile, package.json, pyproject.toml, and YAML files
directly under .github/workflows. Terraform, Bicep, ARM and other IaC formats
remain outside this candidate despite the original carrier's broader name.

## Install and run

Python 3.10 or newer.

~~~sh
python -m pip install .
python -m unittest discover -s tests -t .
~~~

~~~python
from blueprint.core import parse_repository, build_topology, draft_blueprint

canonical = parse_repository("/path/to/repository")
topology = build_topology(canonical)
draft = draft_blueprint(canonical, topology)
~~~

Runtime dependencies are pinned to [PyYAML 6.0.3](https://pypi.org/project/PyYAML/6.0.3/)
and [Tomli 2.4.1](https://pypi.org/project/tomli/2.4.1/). Tomli keeps TOML parsing
consistent across supported Python versions. Dependency advisory results are
recorded in [DEPENDENCY_AUDIT](docs/DEPENDENCY_AUDIT.json).

## Parsing boundaries

Each extracted target retains its source path and SHA-256 of the bytes read.
Commands are descriptive source text, never executable output. Workflow commands
also remain separate in commands; definition retains job settings and
workflow_context retains top-level settings. Conditions, matrices, shell defaults,
reusable workflows and step settings require human interpretation. A declared
environment is not evidence that GitHub requires or received human approval.

JSON/YAML duplicate keys are rejected. YAML aliases, anchors, unsafe object tags,
deep nesting and excessive token counts are rejected. The safe YAML loader keeps
on/off as strings rather than legacy boolean values. Invalid source shapes produce
explicit flags and omit affected targets. Parser diagnostics omit source snippets.
Ordinary commands and definitions can still contain secrets.

Make support covers simple literal single-target rules and tab-indented recipes.
Dynamic/conditional statements are flagged, and inline recipes, order-only
prerequisites, double-colon rules and target-specific assignments are omitted.
No Make expansion, inclusion, conditional evaluation or shell semantics are
implemented. Recipe text is not flattened with shell operators.

## Topology, identity and compatibility

Workflow names are scoped as ci:<workflow filename>:<job id>; dependencies resolve
only within that workflow. Other names retain their prior format. Duplicate global
target names require manual resolution before topology can be built. Unknown
dependencies, cycles and nodes blocked by cycles remain explicit open questions.

Draft generation recomputes the expected topology and requires an exact match to
the supplied model, including its content digest. This prevents accidental mixing
of evidence and topology; it does not authenticate either artifact.

This release changes workflow names from ci:<job id>. Rebuild persisted topology
and draft artifacts. Input validation is stricter; duplicate target identities now
raise ValueError when building topology. The artifact schema identifiers remain v1
with additive metadata.

## Read limits and security

The declared repository root is resolved once. Discovered linked files and
directories, including Windows reparse points, are refused. Only regular UTF-8
source files are read, each at most 1 MiB and at most 16 MiB combined. Discovery
allows up to 256 workflow files and at most 10,000 targets. YAML allows at most
20,001 scanned tokens and 50 nested collections. Canonical models are bounded to
16 MiB. Invalid canonical models raise ValueError.

Use a stable checkout: path checks cannot prevent a concurrent adversary swapping
filesystem entries. The root itself may be a link intentionally supplied by the
caller. Low-level parser functions are not a replacement for parse_repository's
directory boundary. Absolute provenance paths can expose local directory names.

Source text and job metadata are unredacted. Sanitize before sharing or logging.
SHA-256 is content identity, not authentication or authorization. See
[SECURITY](SECURITY.md) for the remaining boundaries.

## Validation and remaining scope

59 tests include 22 inherited checks and 37 new regressions. Source and installed
wheel checks are recorded in [CHECK_RUNS](docs/CHECK_RUNS.json), with the full
[audit](docs/AUDIT.md). CI covers Linux Python 3.10/3.12/3.14 and Windows Python 3.12.

The original carrier's 198 parents and 1,058 children remain a broader roadmap.
Executable pipeline generation, live infrastructure access, IaC parsing, drift
analysis, service layers and production readiness gates are not implemented.
Every draft remains subject to human review.

## License

Copyright 2026 **RUSSELL PHILIP SMITHSON**.
[Apache License 2.0](LICENSE), with [NOTICE](NOTICE).
Dependencies retain their MIT licenses; see [third-party notices](THIRD-PARTY-NOTICES.md).
