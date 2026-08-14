# V1 runner decision and implementation plan

Status: accepted and clean-replayed across Python, Node, and Go; daily discovery
and static publication implemented, 2026-08-14.

## Promise and scope

The V1 promise is: **we actually ran the thing**.

The supported proving tracks are public, open-source, CPU-testable CLIs with
documented local behavior, no required paid service, and no blocking static
finding:

- Python with static PEP 517 metadata and wheel-only dependencies;
- Node with a root `package.json`, declared `bin`, no runtime dependencies, and
  no lifecycle scripts;
- Go with a root `package main`, pinned `go.mod`/`go.sum`, and no `replace`
  directive.

Unsupported projects are recorded as `UNVERIFIED` or skipped; they are never
forced through the wrong track. These boundaries came from the three-repository
proof and are ceilings, not a claim that every repository in an ecosystem is
supported.

The first gate was one real repository from URL to local static review. The
second gate was clean reproduction across Python, Node/TypeScript, and a Go
utility. All three now pass twice from clean environments with the runner and
container-build hashes included in the execution contract.

## Trust boundaries

1. **Collector/inspector:** may hold a read-only GitHub token. It downloads
   metadata and a commit archive but never imports or executes candidate code.
2. **Planner:** receives bounded, sanitized text. Its model process has no tools.
   Output is treated as untrusted JSON and validated against the execution DSL.
3. **Dependency fetcher:** receives validated PyPI requirement strings or only
   `go.mod`/`go.sum`, never candidate source. Python downloads wheels only; Go
   uses `proxy.golang.org` plus the checksum database and exports a hashed local
   module proxy. The bounded Node track has no dependency fetch.
4. **Evaluator:** receives source and the offline dependency bundle but no credentials,
   host mounts, host environment, Docker socket, or network.
5. **Publisher:** consumes only validated JSON and capped text evidence. It does
   not execute candidate artifacts or render candidate HTML/Markdown.

## Sandbox threat model

Candidate code may read its entire container, fork, emit hostile output, exhaust
resources, probe credentials, make network requests, create traversal paths, or
attempt a container escape.

V1 controls:

- exact source commit, archive hash, and extracted-tree hash recorded;
- GitHub commit archives replace host-side Git parsing; candidate `.git` data is
  neither fetched nor staged;
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

The install-time network problem is avoided rather than waved away. Dependency
artifacts are prefetched by a separate container that never receives candidate
source. Node installation uses `--ignore-scripts --offline`. Go compilation uses
a local file proxy, readonly module graph, local toolchain, and disabled CGO.
Candidate code executes only in the network-disabled evaluator. The dependency
tree hash is part of the execution-contract digest, so changed staged artifacts
invalidate cached test results.

Commit archives can omit metadata required by VCS-based build backends. The
Python track detects declared `hatch-vcs`/`setuptools-scm` versioning and supplies
a deterministic `0+worthit.<commit>` build version only inside the evaluator.
This automated deviation is recorded, reduces the setup score, and keeps a
documented registry-install claim at `PARTIAL`.

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

Warm plan repair is mechanically bounded. It cannot change commands, inputs,
setup files, timeouts, claim mappings, or edge labels. Exit-code corrections
must equal the diagnostic exit; output and file assertions cannot be removed or
replaced unless the replacement exists in captured warm evidence. Multiple
failed output guesses may collapse to one replacement only when it covers at
least 80% of an observed output line. A second model critiques the repair before
either cold run.

## Decision rules

- Models propose claims/tests and critique prose; code validates schemas, runs
  commands, evaluates assertions, calculates scores, and enforces publication.
- A test PASS means observed output met its predeclared assertions. The planner
  cannot assign result states.
- Expected non-zero behavior can produce a PASS when the test explicitly probes
  failure handling.
- `BLOCKED` and `UNVERIFIED` never become FAIL or zero without evidence.
- Bullshit Ratio is the importance-weighted share of tested claim mass that
  execution contradicted or materially overstated. Each claim stores its tested
  and unsupported fractions. Unknown portions of `PARTIAL`, blocked, and
  untested claims are excluded rather than counted as false. The exact numerator
  and denominator are stored.
- A review cannot publish without an exact commit, completed risk gate, at least
  one successful core execution and one edge/failure test, evidence hashes,
  deterministic scoring, sufficient confidence, fact-check PASS, and editorial
  PASS.

## Implementation sequence

1. Implement strict GitHub URL parsing, metadata/archive retrieval, safe archive
   extraction, repository evidence discovery, bounded Python/Node/Go metadata
   detection, and static risk classification.
2. Implement validated claim and command-plan schemas plus tool-disabled Claude
   planner and independent critic calls, retaining every input/output artifact.
3. Implement the hardened Docker backend, ecosystem-specific source-free
   dependency staging, source installation/build, capped traces, measurements,
   and teardown.
4. Implement claim evaluation, scores, confidence, setup friction, Bullshit
   Ratio, deterministic review generation, fact checks, prose lint, and escaped
   static HTML.
5. Run controlled hostile fixtures for credential isolation, path traversal,
   output limits, timeout cleanup, unsafe plans, secret redaction, stored XSS,
   and resume behavior.
6. Evaluate established Python, Node, and Go CLIs from exact GitHub commits,
   repair the runner rather than the project, cold-replay each accepted contract,
   and build each review locally.
7. Ask Claude to challenge the plan before execution and independently audit the
   final evidence/review. Resolve valid findings and commit the milestone.
8. Persist GitHub discovery signals, fail closed through metadata and deep gates,
   select at most five distinct repositories per UTC day, and retain the rest in
   the backlog. Bound failed evaluations to two attempts per commit.
9. Rebuild Pages only from schema-validated reviews, approved editorial/fact
   checks, manifest-matched text evidence, and escaped static templates. Verify
   every internal link and reject secret-like data or executable markup.

## Daily and deployment decision

Daily work is three credential-separated jobs. Discovery has read-only GitHub
access and never executes candidate code. Evaluation receives a Claude credential
and read-only GitHub access, but no content-write or Pages permission. A final
recording job receives only the curated `reviews/`, `hunts/`, and `data/`
artifact, rebuilds and sanitizes the site, and alone receives repository write
access. Pages deployment is a fourth job that receives only the sanitized static
artifact and the short-lived Pages/OIDC permissions.

This proves candidate containers cannot obtain publishing credentials through an
environment variable, mount, Docker socket, or job token because those
credentials do not exist in the evaluation job. The runc residual still matters:
an unknown kernel/runtime escape could reach the ephemeral evaluation host and
its model credential. The established-project trust threshold remains mandatory
until a VM-grade backend replaces runc.

Discovery responses are cached for six hours with a bounded stale fallback.
Priority stores its weighted components and repository-specific reasons. Exact
commit failures are not re-qualified until the commit or qualification rules
change; evaluation retries stop at two. A detector-revision change rehashes the
cached source, refreshes derived inspection data, and moves stale private run
state aside before resuming. One operating date is carried across workflow jobs,
so a midnight queue delay cannot create a second daily selection.

Defaults ask the Claude CLI to cap model use at $5 per repository and reserve no
more than $25 per UTC day. WorthIt rejects a response that reports an overage,
but the external provider/CLI remains the authoritative real-time billing
boundary.

## Remaining V1 work

- configure one Claude Actions credential to activate unattended execution;
- replace rootful runc with gVisor, Kata, or a microVM before lowering trust gates;
- add an explicit correction-publication format; same-commit material rewrites
  currently fail closed instead of replacing historical evidence;
- paid APIs, GPUs, service containers, browsers, SaaS, and arbitrary Dockerfiles;
- candidate-controlled direct URLs or source distributions as dependencies;
- repair by changing candidate business source;
- cross-project performance claims without a comparable baseline.

The broader execution tracks stay deferred until evidence shows they are needed
and a stronger isolation backend is available.

## Three-repository failure comparison

Observed bring-up failures, not hypothetical ones:

| Track | Repository | Failure | Generalized correction |
|---|---|---|---|
| Python | PyCQA/isort | GitHub's archive omitted VCS metadata required by the declared version backend; one generated edge assertion guessed the wrong exit code. | Detect declared VCS versioning, inject and disclose a deterministic sandbox-only version, mechanically constrain warm correction, and require two cold replays. |
| Node | mishoo/UglifyJS | A CI-only `sudo` command triggered an execution-path risk block; generated source-map and cross-file assertions guessed intermediate output; editorial drafts hid a MEDIUM finding and leaked repair history. | Preserve the risk finding but grade non-executed CI context separately, require near-complete observed-line replacements plus independent criticism, surface every risk/coverage gap, and reject reviewer-history prose. |
| Go | tomnomnom/gron | Docker `cp` could not write manifests through a read-only container root despite a tmpfs destination. Cold compilation also made setup materially slower than interpreted-package installs. | Transfer capped inert manifests over stdin to fixed tmpfs paths as UID 65534, keep source out of the network phase, hash the local module proxy, and report build time/toolchain independently. |

Only these demonstrated differences were generalized. Node remains limited to
dependency-free, lifecycle-free packages; Go remains limited to a root command
with a pinned module graph. Execution-cache identity includes the accepted plan,
install environment, dependency tree, immutable image ID, Dockerfile hash,
runner-code hash, runtime, and limits.

## Iteration status and next gate

Completed on 2026-08-14:

- prior-art and license research;
- static GitHub inspection, Python CLI detection, claims, plan, independent
  critique, and objective provenance/risk gate;
- offline wheel preparation separated from candidate source;
- hardened disposable runner with clean replay, measurements, redaction, and
  teardown;
- deterministic claim matrix, scoring, confidence, Bullshit Ratio, review,
  fact check, editorial critique, and escaped static publication;
- finalized isort evaluation with 8/8 accepted tests reproduced twice;
- UglifyJS evaluation with 6/6 accepted tests reproduced twice, including file,
  STDIN, output-file, source-map, cross-file, and malformed-input workflows;
- gron evaluation with 7/7 accepted tests reproduced twice, including file,
  stdin, JSON-stream, reverse-conversion, filtering, and invalid-input paths;
- all three evaluator images passed the live mount, secret, socket, network,
  timeout, and teardown isolation probe.

The finalized runner clean-reran all three repositories with identical
per-repository execution contracts and dependency-bundle hashes. Daily mode may
now select only repositories that pass the existing established-project trust
threshold. Stronger-than-runc isolation remains required before lowering that
threshold or broadening the supported execution tracks.
