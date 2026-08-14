# V1 runner decision and implementation plan

Status: accepted for the first real evaluation, 2026-08-14.

## Promise and scope

The V1 promise is: **we actually ran the thing**.

The first supported track is a public, open-source, CPU-testable Python CLI with
static PEP 517 metadata, PyPI wheel dependencies, documented local behavior, no
required paid service, and no suspicious static findings. Unsupported projects
are recorded as `UNVERIFIED` or skipped; they are never forced through the wrong
track.

The first gate is one real repository from URL to local static review. The next
gate is clean reproduction across Python, Node/TypeScript, and a Rust/Go utility.
Broad daily execution stays disabled until all three pass twice from clean
environments.

## Trust boundaries

1. **Collector/inspector:** may hold a read-only GitHub token. It downloads
   metadata and a commit archive but never imports or executes candidate code.
2. **Planner:** receives bounded, sanitized text. Its model process has no tools.
   Output is treated as untrusted JSON and validated against the execution DSL.
3. **Dependency fetcher:** receives validated PyPI requirement strings but no
   candidate source and downloads wheels only.
4. **Evaluator:** receives source and the offline wheelhouse but no credentials,
   host mounts, host environment, Docker socket, or network.
5. **Publisher:** consumes only validated JSON and capped text evidence. It does
   not execute candidate artifacts or render candidate HTML/Markdown.

## Sandbox threat model

Candidate code may read its entire container, fork, emit hostile output, exhaust
resources, probe credentials, make network requests, create traversal paths, or
attempt a container escape.

V1 controls:

- exact source commit and archive hash recorded;
- static source/install/CI scan before execution;
- candidate runs as a numeric non-root user;
- no host bind mounts and no Docker socket;
- allowlisted environment built from scratch;
- root filesystem read-only; bounded tmpfs for source, environment, and outputs;
- all Linux capabilities dropped and `no-new-privileges` set;
- private PID/cgroup/network namespaces, with candidate network set to `none`;
- CPU, memory, process, output, command, total-time, and retry limits;
- Docker commands are argument arrays, not shell interpolation;
- evidence is copied through capped stdout/stderr and explicit safe-file reads;
- container removal runs in `finally`, and stale labeled containers are reaped;
- secret redaction occurs before evidence is written;
- publication accepts only escaped text and JSON from allowlisted paths.

Residual risk: the available local Docker runtime is rootful `runc` with seccomp,
AppArmor, and cgroup namespaces. It is not a VM boundary. A kernel/runtime escape
could reach the host. Until gVisor, Kata, or a microVM backend is available, only
well-established repositories classified `TRUSTED_ENOUGH_TO_TEST` may run, and
the operator must explicitly acknowledge this backend. `TEST_WITH_RESTRICTIONS`
and lower classifications fail closed.

The install-time network problem is avoided rather than waved away: dependency
wheels are prefetched by a separate container that never receives candidate
source. Candidate build hooks execute only in the offline evaluator.

## Execution contract

One immutable JSON run owns:

- repository identity, commit, source hash, metadata, and detected environment;
- repository brief and evidence excerpts;
- falsifiable claims with source, importance, and testability;
- initial plan, independent critique, accepted plan, and plan provenance;
- risk findings and trust decision;
- stages: inspect, plan, risk, provision, install, verify-install, execute,
  measure, collect, destroy, evaluate, review, fact-check, editorial, publish;
- per-command argv, cwd, timeout, expected observation, and evidence request;
- traces with exit code, duration, capped stdout/stderr, peak RAM where
  practical, disk delta, retry count, and failure reason;
- claim matrix, score dimensions, confidence, Bullshit Ratio inputs, and verdict;
- review and publication decision.

State and stage outputs use atomic JSON writes. Completed stages are reused only
when their input digest still matches. Evidence files are content-hashed. A cold
replay creates a new disposable container and must pass the accepted contract;
warm repair evidence alone cannot support publication.

## Decision rules

- Models propose claims/tests and critique prose; code validates schemas, runs
  commands, evaluates assertions, calculates scores, and enforces publication.
- A test PASS means observed output met its predeclared assertions. The planner
  cannot assign result states.
- Expected non-zero behavior can produce a PASS when the test explicitly probes
  failure handling.
- `BLOCKED` and `UNVERIFIED` never become FAIL or zero without evidence.
- Bullshit Ratio is the importance-weighted share of tested claims that are
  unsupported, counting `FAIL` as 1 and `PARTIAL` as 0.5; blocked and untested
  claims are excluded. The numerator and denominator are stored.
- A review cannot publish without an exact commit, completed risk gate, at least
  one successful core execution and one edge/failure test, evidence hashes,
  deterministic scoring, sufficient confidence, fact-check PASS, and editorial
  PASS.

## Implementation sequence

1. Implement strict GitHub URL parsing, metadata/archive retrieval, safe archive
   extraction, repository evidence discovery, Python metadata detection, and
   static risk classification.
2. Implement validated claim and command-plan schemas plus tool-disabled Claude
   planner and independent critic calls, retaining every input/output artifact.
3. Implement the hardened Docker backend, offline wheel prefetch, source staging,
   install/verify/test execution, capped traces, measurements, and teardown.
4. Implement claim evaluation, scores, confidence, setup friction, Bullshit
   Ratio, deterministic review generation, fact checks, prose lint, and escaped
   static HTML.
5. Run controlled hostile fixtures for credential isolation, path traversal,
   output limits, timeout cleanup, unsafe plans, secret redaction, stored XSS,
   and resume behavior.
6. Evaluate one established Python CLI from its exact GitHub commit, repair the
   runner rather than the project, cold-replay the accepted contract, and build
   the review locally.
7. Ask Claude to challenge the plan before execution and independently audit the
   final evidence/review. Resolve valid findings and commit the milestone.

## Explicit deferrals

- daily discovery/selection and unattended top-five execution;
- production Pages deployment and Daily Hunt reports;
- paid APIs, GPUs, service containers, browsers, SaaS, and arbitrary Dockerfiles;
- candidate-controlled direct URLs or source distributions as dependencies;
- repair by changing candidate business source;
- cross-project performance claims without a comparable baseline.

These return only after the three-repository clean-replay gate.
