# Audit and hardening — 0.1.2a1

Date: 2026-09-23. Source: JY-S007-P001 / 0.1.1-partial / run-0001 / product.
Reviewed all parser, topology, draft and inherited test code. Original source
remains separate from the maintenance checkout.

## Repaired findings

- Jobs with equal names across workflow files collided, allowing cross-workflow
  dependency resolution and lost traceability. Workflow filenames now scope names.
- Duplicate JSON/YAML keys silently overwrote evidence. Strict loaders now reject
  duplicates, unsafe tags, aliases, excess YAML nesting/tokens and nonfinite data.
- Malformed script commands, TOML tables, step/needs/environment types and reusable
  workflows could crash or produce misleading targets. Shape validation now flags
  and omits invalid targets; reusable references remain explicit and unexpanded.
- YAML 1.1 implicit boolean handling changed job names such as on/off. Loader
  boolean spelling now follows the true/false convention for workflow identities.
- Commands from separate recipe/step contexts were joined with shell &&,
  fabricating semantics. Lines remain separate, with job and workflow context kept.
- Unsupported Make constructs could masquerade as literal dependencies. These
  now raise review flags or omit affected targets, without echoing raw snippets.
- Reads followed links without size limits and left files unclosed. Bounded,
  managed reads reject linked paths/reparse points, nonregular files and invalid
  UTF-8; discovery and total input are bounded. Concurrent filesystem races remain.
- Duplicate target identities collapsed traceability, cycles were invisible, and
  arbitrary supplied topology could be paired with canonical evidence. Topology
  now validates identities, exposes cycles/blocked nodes and binds the full model;
  draft generation recomputes and requires the exact expected topology.
- TOML import required Python 3.11 despite no declared support boundary. Packaging
  now declares >=3.10 and uses pinned Tomli consistently across platforms.

## Verification and release

22 inherited tests passed before changes. Workflow-name assertions were updated
for the intentional naming change. 59 source/installed-wheel tests now pass,
including 37 regressions. CI covers Linux Python 3.10/3.12/3.14 and Windows 3.12.
The symlink regression simulates link metadata portably; it does not test concurrent
path swapping. Historical check evidence remains separate.

Pinned PyYAML 6.0.3 and Tomli 2.4.1 were checked against PyPI release metadata
(https://pypi.org/project/PyYAML/ and https://pypi.org/project/tomli/).
pip-audit reported no known vulnerabilities for these installed versions; results
are in DEPENDENCY_AUDIT.json. No build-tool vulnerability scan is claimed.

Version 0.1.1-partial -> 0.1.2a1. Rebuild persisted workflow IDs, topology and drafts.
Added packaging, pinned-action CI, README, security boundaries and Apache 2.0
LICENSE/NOTICE naming RUSSELL PHILIP SMITHSON. Dependencies retain MIT licensing.
This remains a partial read-only candidate, not a production readiness decision.
