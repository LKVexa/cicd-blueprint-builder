# Security boundaries

The package reads a supplied checkout and returns draft dictionaries. It never
executes commands, loads Python objects from YAML, deploys or approves anything.
Parsed source is untrusted descriptive data. Do not execute command strings
from a draft or render them as unsanitized HTML/shell content.

parse_repository checks regular files, UTF-8, size/count limits and linked path
components. Use a stable checkout or isolated snapshot: validation followed by
opening a file is not race-proof against concurrent filesystem changes. The
caller-authorized root is resolved; low-level parser functions lack the full
repository discovery boundary. OS quotas are needed for hostile workloads.

Duplicate structured keys and YAML aliases are refused. Some valid Make/YAML
features are intentionally unsupported. Flags, unresolved dependencies and cycle
questions require human review. Successful parsing does not validate a workflow
against every GitHub rule, authenticate actions, evaluate expressions, or prove
the repository's runtime behavior.

Raw commands, job definitions, workflow context and absolute provenance paths
can reveal sensitive information. Parser diagnostics omit raw exception text,
but normal extracted data is not redacted. The canonical digest binds model
content; it is not a signature or a guarantee of genuine source files.

Dependencies are pinned and scanned for known advisories at release. A clean
scan does not establish absence of vulnerabilities. No build-tool scan is claimed.
Report defects privately with sanitized synthetic files.
