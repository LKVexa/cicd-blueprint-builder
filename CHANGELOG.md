# 0.1.2a1 — 2026-09-23

- Scope workflow identities to source files; reject duplicate target identities.
- Reject duplicate JSON/YAML keys and unsafe/ambiguous YAML structures.
- Validate parser shapes, bound reads and discovery, reject linked source paths.
- Preserve recipe/step separation and workflow context; flag unsupported syntax.
- Expose cycles and verify draft topology against its canonical evidence.
- Pin PyYAML/Tomli, add 37 regressions, packaging, Apache licensing and CI.
- Compatibility: regenerate workflow names, topology and drafts.

# Changelog — CI/CD & IaC Blueprint Builder (JY-S007-P001)

## 0.1.1-partial — 2026-09-14 (maintenance audit A008)

Baseline fingerprint: build-0001 product.zip
sha256 498544f08460310889f6ac9e9f8263fcc5139ac8d5ff2a351560d0c5440fb7a8
(baseline version 0.1.0-partial; baseline suite 13/13 PASS before changes).
All findings below were reproduced live on the unmodified baseline before
fixing (probe log retained in audit records).

### Fixes (repairs only — patch bump)

- **A008-F1 — output aliasing corrupts the canonical model.**
  Observed: mutating `draft_blueprint()` output (`traceability` provenance
  dicts, `pipeline.gates[].gate`, `open_questions` entries, `edges`) or
  `build_topology()` gates mutated the canonical artifact model in place —
  provenance digests could be silently rewritten after parsing.
  Expected: parser output is the provenance record; downstream artifacts
  must be isolated copies. Fixed by deep-copying shared mutable structures
  at the topology and blueprint boundaries.
- **A008-F2 — crash on structurally invalid package.json.**
  Observed: valid-JSON-but-non-object (`[1,2]`) or non-object `scripts`
  raised bare `AttributeError`. Expected: flagged as unsupported/ambiguous
  per the no-guessing contract. Fixed: flagged, parsing continues.
- **A008-F3 — crash on structurally invalid workflow YAML.**
  Observed: `jobs` as a list raised `AttributeError`; a job with
  non-list `steps` raised `TypeError`, aborting the whole repository parse.
  Expected: flag the construct and keep parsing other jobs. Fixed.
- **A008-F4 — bare FileNotFoundError on missing repository root.**
  Observed: `parse_repository('/missing')` leaked `FileNotFoundError`
  from `os.listdir`. Expected: a clear documented error contract.
  Fixed: raises `ValueError("repository root is not a directory: ...")`.
- **A008-F5 — duplicate Makefile targets silently collapse.**
  Observed: two definitions of the same target produced two targets whose
  traceability entries silently collapsed to one, with no flag. Expected:
  ambiguity is flagged, never guessed. Fixed: duplicate target definitions
  are flagged in `flagged_constructs`.

### Compatibility

No public API removed or renamed; all baseline behavior for well-formed
inputs is unchanged (all 13 baseline tests still pass unmodified). New
behavior only on previously-crashing or previously-silent inputs, plus the
`ValueError` contract for a nonexistent root (previously `FileNotFoundError`).

### Rollback

Restore build-0001 product.zip (sha256 above); no data formats or
persistent state involved (the builder is stateless and read-only).
