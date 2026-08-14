# gron: WorthIt verification review

- Repository: [tomnomnom/gron](https://github.com/tomnomnom/gron)
- Commit tested: `88a6234ea2d0c487090988182ad9a7cdf6def924`
- Tool version output: [`dev`](evidence/final/version/stdout.txt)
- Date tested: 2026-08-14
- Category: CLI utility
- WorthIt Score: 86/100
- Confidence: MEDIUM
- Verdict: WORTH IT
- Bullshit Ratio: 0%

## TL;DR

The exact commit completed offline Go source build against a staged checksum-verified module proxy in 56.42 seconds with no manual intervention. 7 of 7 accepted tests passed with the same results in a second clean, offline container. Claim coverage gaps: 3 partial, 1 unverified. Evidence confidence is medium.

## Claim matrix

### CLAIM-001: UNVERIFIED

> gron has no runtime dependencies and provides downloadable pre-built binaries for Linux, Mac, Windows, and FreeBSD.

Source: `README.mkd`. Tests: installation stage / no direct test. No accepted test exercised this claim.

### CLAIM-002: PARTIAL

> gron can be installed via `go install github.com/tomnomnom/gron@latest`.

Source: `README.mkd`. Tests: installation stage / no direct test. The exact commit completed offline Go source build against a staged checksum-verified module proxy, but the documented registry command was not run verbatim.

### CLAIM-003: PASS

> Running gron on a JSON input transforms it into discrete assignment statements representing the absolute path to each value.

Source: `README.mkd`. Tests: T01. All mapped tests passed in both clean replays.

### CLAIM-004: PASS

> gron supports converting its own assignment-statement output back into the original JSON structure using the `--ungron`/`-u` flag.

Source: `README.mkd`. Tests: T03, T05. All mapped tests passed in both clean replays.

### CLAIM-005: PASS

> gron supports a `--json` flag that outputs data as a JSON stream instead of the default assignment-statement format.

Source: `README.mkd`. Tests: T04. All mapped tests passed in both clean replays.

### CLAIM-006: PARTIAL

> gron accepts a JSON source as a command-line argument that can be a local file path, a URL, or stdin (via `-`).

Source: `README.mkd`. Tests: T01, T02. Tests T01, T02 passed in both clean replays. Remaining gap: The URL-argument and stdin-URL-fetch portion of CLAIM-006 is not tested since it requires live network access; only the local-file and stdin-pipe input modes are exercised.

### CLAIM-007: PARTIAL

> gron returns specific documented exit codes (0-6) corresponding to success or particular failure modes (e.g., 1 for failed to open file, 4 for failed to fetch URL).

Source: `README.mkd`. Tests: T06, T07. Tests T06, T07 passed in both clean replays. Remaining gap: T07 accepts either exit code 2 or 3 for malformed JSON rather than asserting a single value, since the README's exit-code table does not specify whether JSON syntax errors are classified as a read failure or a statement-formation failure; both are treated as passing evidence that gron correctly rejects invalid input per CLAIM-007.

## What we ran

- T01 (core): PASS, exit 0, 1768 ms, [stdout](evidence/final/T01/stdout.txt), [stderr](evidence/final/T01/stderr.txt). Verify gron transforms a JSON file into sorted, discrete assignment statements representing the absolute path to each value.
- T02 (core): PASS, exit 0, 1771 ms, [stdout](evidence/final/T02/stdout.txt), [stderr](evidence/final/T02/stderr.txt). Verify gron accepts JSON via stdin (no file/URL argument) as documented.
- T03 (core): PASS, exit 0, 1769 ms, [stdout](evidence/final/T03/stdout.txt), [stderr](evidence/final/T03/stderr.txt). Verify gron --ungron reverses assignment statements back into the original JSON structure.
- T04 (core): PASS, exit 0, 1762 ms, [stdout](evidence/final/T04/stdout.txt), [stderr](evidence/final/T04/stderr.txt). Verify gron --json emits data as a JSON-array-per-line stream instead of assignment statements.
- T05 (edge/failure): PASS, exit 0, 1774 ms, [stdout](evidence/final/T05/stdout.txt), [stderr](evidence/final/T05/stderr.txt). Verify ungron pads missing array indices with null to preserve array keys, as documented.
- T06 (edge/failure): PASS, exit 1, 1780 ms, [stdout](evidence/final/T06/stdout.txt), [stderr](evidence/final/T06/stderr.txt). Verify gron exits with documented code 1 and reports a diagnostic when the input file cannot be opened.
- T07 (edge/failure): PASS, exit 3, 1778 ms, [stdout](evidence/final/T07/stdout.txt), [stderr](evidence/final/T07/stderr.txt). Verify gron exits with a documented failure code (2 'Failed to read input' or 3 'Failed to form statements') when given syntactically invalid JSON, rather than exiting 0.

## Setup experience

Setup friction: EASY. The final clean install took 56.42 seconds; the replay took 56.44 seconds. Manual interventions: 0. Candidate network access: none.

WorthIt compiled the exact staged commit with go build against a local checksum-verified module proxy; it did not download a release binary or run the README's go install command verbatim.

GitHub's latest-release metadata was v0.7.1, while the tested commit-archive source build reported dev. WorthIt built this Go commit rather than testing a published release binary; the commit SHA identifies the artifact.

## Performance

The slowest accepted test took 1780 ms. Peak measured test RAM was 126.0 MiB. No comparative baseline was run, so this is a measurement, not a speed claim.

## What broke

- No accepted final or replay test failed.
- The accepted contract did not require diagnostic repair.
- Not fully verified: CLAIM-002, gron can be installed via `go install github.com/tomnomnom/gron@latest`.
- Not fully verified: CLAIM-006, gron accepts a JSON source as a command-line argument that can be a local file path, a URL, or stdin (via `-`).
- Not fully verified: CLAIM-007, gron returns specific documented exit codes (0-6) corresponding to success or particular failure modes (e.g., 1 for failed to open file, 4 for failed to fetch URL).
- Unverified: CLAIM-001, gron has no runtime dependencies and provides downloadable pre-built binaries for Linux, Mac, Windows, and FreeBSD.

## Scorecard

| Dimension | Score | Evidence-based reason |
|---|---:|---|
| Claim Verification | 86/100 | Importance-weighted claim results; unverified claim portions reduce coverage rather than becoming failures. |
| Utility | 85/100 | 4/4 core workflow tests (T01, T02, T03, T04) passed. |
| Setup Experience | 90/100 | offline Go source build against a staged checksum-verified module proxy took 56421 ms with 0 manual interventions and 0 automated setup adjustments. |
| Reliability | 95/100 | Two clean runs compared across 7 tests; reproduced=True. |
| Performance Efficiency | 85/100 | Slowest test was 1780 ms and measured peak RAM was 132120576 bytes; no comparative baseline was used. |
| Documentation | 90/100 | README-derived workflows worked without contract repair. |
| Safety Privacy | 90/100 | Static screening recorded no findings; candidate execution had no network. Static review is not a safety guarantee. |
| Novelty | 50/100 | Neutral score: WorthIt did not run a comparative novelty study. |

## Who should use it

- Developers evaluating gron as make JSON greppable!.
- Teams that need the tested CLI workflow to run locally without candidate-time network access.

## Who should skip it

- This review is not enough if your decision requires full verification of: CLAIM-002: gron can be installed via `go install github.com/tomnomnom/gron@latest`; CLAIM-006: gron accepts a JSON source as a command-line argument that can be a local file path, a URL, or stdin (via `-`); CLAIM-007: gron returns specific documented exit codes (0-6) corresponding to success or particular failure modes (e.g., 1 for failed to open file, 4 for failed to fetch URL); CLAIM-001: gron has no runtime dependencies and provides downloadable pre-built binaries for Linux, Mac, Windows, and FreeBSD.

## Reproduction and safety

Source archive SHA-256: `9199144da889c8f313b56dd2e6346e380a18945e0bf7648d62b1c4dc4c5660ca`. The exact commit was run twice with candidate network disabled. The backend was docker/runc with seccomp and AppArmor; runc shares the host kernel. This is not a guarantee that the repository is safe.

Structured provenance: [risk assessment](risk.json), [sandbox environment](environment.json), [dependency fetch](dependency-fetch.json), and [reproducibility comparison](reproducibility.json).

## Limitations

- CLAIM-001 (no-runtime-dependency pre-built binaries for Linux/Mac/Windows/FreeBSD) is not tested: the runner builds from source offline and does not download or execute GitHub release binaries.
- T06's stderr assertion only checks that the missing filename appears in the diagnostic, since the exact wording of gron's 'failed to open file' message is not specified in the documentation.
- GitHub's latest-release metadata was v0.7.1, while the tested commit-archive source build reported dev. WorthIt built this Go commit rather than testing a published release binary; the commit SHA identifies the artifact.
