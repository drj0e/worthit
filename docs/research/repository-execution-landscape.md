# Repository execution landscape

Research date: 2026-08-14

WorthIt's immediate problem is narrower than "run any repository": given a
well-documented, CPU-testable CLI or developer tool, derive a setup contract,
replay it in a clean sandbox, run claim-linked tests, and retain enough evidence
to defend the review.

This survey used primary project pages, papers, and source repositories. The
three source repositories were inspected statically at these commits; none of
their code was executed:

- RepoLaunch: `95a49fe87ea31c8367e5a213e2221f17ccb9b27d`
- BootstrapAgent: `a5ea466b23534d20baa97f12f3ec1f0071a38ac5`
- Runabilly: `65d961f68a49b6a8fc07f0254964b02b8460ea4c`

## GitHub Next Discovery Agent

Sources: [project page](https://githubnext.com/projects/discovery-agent/) and
the cited [ExecutionAgent paper](https://arxiv.org/abs/2412.10133).

Discovery Agent is a research prototype that uses a ReAct-style loop to inspect,
set up, build, and test a repository in Docker or Codespaces. It gives the model
shell and explicit file tools, points it toward README, CI, devcontainer, and
build metadata, and iterates on execution feedback. Its published account calls
out three practical details: detect stuck or interactive commands from output
progress, summarize only oversized command output, and distill exploration into
one replay script with delineated setup/build/test sections. It also distinguishes
"there are no tests" from "tests were not run."

WorthIt can reuse those ideas conceptually: prioritized evidence discovery,
non-interactive execution, bounded output, progress/timeout detection, and a
single coherent replay contract. WorthIt should not adopt an open-ended shell
loop as its verifier. The verifier must remain deterministic and the model must
not judge its own success.

The project page does not link a released implementation or a source license.
That makes direct code reuse unavailable. The page also names secrets, service
dependencies, and multi-language repositories as open problems, so it is not a
security blueprint for hostile repositories.

## RepoLaunch

Sources: [paper](https://arxiv.org/abs/2603.05026), [repository](https://github.com/microsoft/RepoLaunch),
and [development documentation](https://github.com/microsoft/RepoLaunch/blob/main/docs/Development.md).

RepoLaunch separates repository bootstrapping into three stages:

1. Preparation scans the file tree and likely setup documents, chooses a
   language/platform base image, and starts a container.
2. Build lets a setup agent resolve dependencies and compile, then lets a
   separate verifier inspect real test output before accepting the image.
3. Management removes failed or redundant exploration commands, replays the
   minimal rebuild path, prefers structured test output, and generates a test
   log parser and optional per-test commands.

The useful ideas for WorthIt are the explicit preparation/build/management
separation, language-specific hints, independent verification, command-history
distillation, and preference for JSON/XML test output over prose parsing.
RepoLaunch also shows why a build alone is weak evidence: a reusable environment
needs a verified rebuild command and observable test status.

The repository is MIT licensed, so direct reuse is legally possible with the
license notice. WorthIt will not vendor it in V1. It brings a large LLM/agent
stack and its runtime assumptions conflict with WorthIt's hostile-code policy:
the inspected Linux runtime uses ordinary Docker, a writable host bind mount,
`host.docker.internal`, a root-like interactive shell, and general network
access. Its verifier can accept a majority of tests, whereas a WorthIt claim test
must retain each PASS/PARTIAL/FAIL/BLOCKED/UNVERIFIED result separately.

## BootstrapAgent

Sources: [paper](https://arxiv.org/abs/2605.15815), [repository](https://github.com/Vossera/BootstrapAgent),
and its [method overview](https://github.com/Vossera/BootstrapAgent/blob/main/code/overview.md).

BootstrapAgent treats setup knowledge as a persistent `.bootstrap` contract.
It extracts evidence from README, CI, package metadata, lockfiles, scripts, and
project structure; proposes install, doctor, minimal verification, strongest
locally reproducible verification, and run-probe commands; executes them in a
deterministic Docker verifier; then repairs from normalized traces. Its strongest
ideas are:

- preserve command provenance rather than just the final shell text;
- distinguish installability, testability, and runnability;
- use a warm environment to diagnose cheaply but require a cold clean replay;
- retain failure signatures and repair history;
- prevent "repair" from weakening the strongest verification into a vacuous
  green check;
- cap commands, repair loops, repeated failures, and wall time.

WorthIt will adopt those concepts in a smaller JSON execution contract. A
successful warm attempt is not publishable until the same accepted contract
passes from a clean environment.

The released code and documentation are MIT licensed. Direct reuse is possible
with the notice, but V1 will remain clean-room and standard-library-only. The
inspected verifier bind-mounts the repository and logs, forwards proxy variables,
and uses ordinary Docker without the CPU, memory, process, read-only filesystem,
secret, and artifact controls WorthIt requires. Its command safety regexes are
useful screening ideas, not a security boundary.

## Runabilly

Source: [repository](https://github.com/OBF/runabilly).

Runabilly is a small Claude-driven workflow for building open-source projects in
a disposable Ubuntu container. It performs Docker capacity checks, clones with
Git LFS payloads disabled by default, probes a broad list of build markers,
consults README and CI, allows up to three substantive repair attempts, requires
project tests when present, records setup divergence, and guarantees cleanup
unless an operator explicitly keeps the container.

WorthIt can reuse the conceptual build-marker list, LFS opt-in, divergence/setup
friction measurements, substantive-retry rule, and cleanup discipline.

Runabilly is BSD-3-Clause licensed, so direct reuse would require retaining its
copyright, conditions, disclaimer, and non-endorsement condition. WorthIt will
not copy it. The inspected runtime gives the container full outbound Internet,
runs a broad root-capable Ubuntu environment, exposes no CPU/process/storage
limit beyond a memory cap, and relies on ordinary Docker. Those choices fit an
interactive build helper, not a hostile-code verification lab.

## Architecture decision

WorthIt will implement the shared good idea, not import any of the systems:

```text
static repository evidence
  -> constrained execution contract with provenance
  -> independent plan critique
  -> static risk gate
  -> warm sandbox run with structured traces
  -> cold clean replay of the accepted contract
  -> claim matrix and deterministic score
  -> mechanically fact-checked review
```

The V1 contract is deliberately narrower than a generated shell script. Commands
are argument arrays with an explicit working directory, timeout, network policy,
resource budget, expected observable result, and evidence list. No command is
run by a host shell. Python CLI repositories are the first supported execution
track; Node and compiled single-binary tracks wait for the three-repository gate.

Repository archives are downloaded and inspected as data on the orchestrator.
Candidate code first runs only after the risk decision, inside a disposable
non-root container with no bind mounts, no inherited environment, no Docker
socket, no network during candidate execution, a read-only root filesystem,
tmpfs workspaces, dropped capabilities, `no-new-privileges`, CPU/RAM/process/time
limits, capped output, and unconditional teardown. Package wheels are fetched in
a separate networked preparation container that never receives candidate source;
the candidate install consumes that wheelhouse offline.

Ordinary Docker still shares the host kernel. V1 therefore only executes
`TRUSTED_ENOUGH_TO_TEST` benign repositories and records the residual risk. The
backend is required to fail closed when its hardening capabilities are missing.
An ephemeral VM plus gVisor or a microVM is the next isolation backend before
testing lower-trust repositories unattended.

## What remains WorthIt-specific

The surveyed systems try to make repositories buildable. WorthIt additionally
needs to establish promotional claims, design fair user-workflow and edge tests,
capture measurements and immutable evidence, map claims to results, distinguish
unknown from failure, calculate confidence and the Bullshit Ratio, and publish a
review whose factual assertions are mechanically traceable. None of the surveyed
implementations provides that full chain.
