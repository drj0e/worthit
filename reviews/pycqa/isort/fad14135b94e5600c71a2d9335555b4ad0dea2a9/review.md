# isort: WorthIt verification review

- Repository: [PyCQA/isort](https://github.com/PyCQA/isort)
- Commit tested: `fad14135b94e5600c71a2d9335555b4ad0dea2a9`
- Tool version output: [`0+worthit.fad14135b94e`](evidence/final/version/stdout.txt)
- Date tested: 2026-08-14
- Category: CLI utility
- WorthIt Score: 84/100
- Confidence: HIGH
- Verdict: WORTH IT
- Bullshit Ratio: 0%

## TL;DR

The exact commit installed from source in 10.06 seconds with no manual intervention and 1 automated VCS-version adjustment. 7 of 7 accepted tests passed with the same results in a second clean, offline container. 3 claims remain only partially verified. Evidence confidence is high.

## Claim matrix

### CLAIM-001: PARTIAL

> Running `pip install isort` installs the isort package.

Source: `README.md`. Tests: T01. The exact commit installed successfully with pip from staged source, but the registry command was not run verbatim. An automated VCS-version fallback was required.

### CLAIM-002: PASS

> Running `isort mypythonfile.py mypythonfile2.py` sorts the imports in those specific files.

Source: `README.md`. Tests: T01. All mapped tests passed in both clean replays.

### CLAIM-003: PASS

> Running `isort .` applies isort recursively to the current directory.

Source: `README.md`. Tests: T02. All mapped tests passed in both clean replays.

### CLAIM-004: PASS

> Running `isort mypythonfile.py --diff` shows proposed changes without applying them to the file.

Source: `README.md`. Tests: T03. All mapped tests passed in both clean replays.

### CLAIM-005: PARTIAL

> Running `isort --atomic .` runs isort against a project and only applies changes if they don't introduce syntax errors.

Source: `README.md`. Tests: T06. Tests T06 passed in both clean replays. Remaining gap: T06 was corrected from the original plan: warm evidence showed isort --atomic exits 0 (not 1) when the target file has a syntax error, while still leaving the file byte-for-byte unchanged and emitting a UserWarning on stderr ('unable to sort due to existing syntax errors'). The expected exit code was ungrounded speculation in the original plan and is now corrected to match repeatable observed behavior; the file-unchanged and stderr-warning assertions remain load-bearing and directly test the CLAIM-005 no-changes-on-syntax-error behavior, so the assertion was not weakened, only the incorrect exit-code expectation was fixed.

### CLAIM-006: PASS

> Running isort with `-c` (check-only) verifies formatting and outputs incorrectly sorted files to stderr rather than modifying them.

Source: `README.md`. Tests: T04, T05. All mapped tests passed in both clean replays.

### CLAIM-007: PARTIAL

> isort requires Python 3.10 or higher to run.

Source: `pyproject.toml`. Tests: T07. Tests T07 passed in both clean replays. Remaining gap: CLAIM-007 (Python 3.10+ requirement) is covered only indirectly via installed package metadata (T07); the test runner executes in a single fixed Python environment, so actually attempting installation/execution under Python <3.10 to confirm rejection is not feasible here.

### CLAIM-008: PASS

> Installing the isort package registers an `isort` command-line script entry point mapped to isort.main:main.

Source: `pyproject.toml`. Tests: T01. All mapped tests passed in both clean replays.

## What we ran

- T01 (core): PASS, exit 0, 1781 ms, [stdout](evidence/final/T01/stdout.txt), [stderr](evidence/final/T01/stderr.txt). Verify isort sorts imports in two specific files passed as separate positional arguments, using distinct import sets per file so the test can detect if isort mixes up, skips, or cross-applies results between files.
- T02 (core): PASS, exit 0, 1288 ms, [stdout](evidence/final/T02/stdout.txt), [stderr](evidence/final/T02/stderr.txt). Verify `isort .` recursively finds and sorts imports in Python files inside subdirectories of the current directory.
- T03 (core): PASS, exit 0, 1287 ms, [stdout](evidence/final/T03/stdout.txt), [stderr](evidence/final/T03/stderr.txt). Verify `isort file.py --diff` prints a unified diff of the proposed change to stdout while leaving the file untouched.
- T04 (core): PASS, exit 1, 1289 ms, [stdout](evidence/final/T04/stdout.txt), [stderr](evidence/final/T04/stderr.txt). Verify `isort -c` on a file with incorrectly sorted imports reports the problem on stderr, exits nonzero, and leaves the file unmodified.
- T05 (core): PASS, exit 0, 1310 ms, [stdout](evidence/final/T05/stdout.txt), [stderr](evidence/final/T05/stderr.txt). Verify `isort -c` on an already correctly sorted file succeeds with exit code 0 and does not modify the file, complementing the failure case in T04.
- T06 (edge/failure): PASS, exit 0, 1287 ms, [stdout](evidence/final/T06/stdout.txt), [stderr](evidence/final/T06/stderr.txt). Invalid-input/failure case: run `isort --atomic` on a file containing a Python syntax error and confirm isort refuses to apply changes (leaves the file byte-for-byte unchanged) and warns that sorting was skipped due to the syntax error, since applying changes could not be verified to preserve valid syntax.
- T07 (core): PASS, exit 0, 1282 ms, [stdout](evidence/final/T07/stdout.txt), [stderr](evidence/final/T07/stderr.txt). Give CLAIM-007 (Python 3.10+ requirement) indirect coverage by inspecting the installed isort package's published Requires-Python metadata, confirming the >=3.10.0 constraint declared in pyproject.toml is actually shipped in the installed distribution.

## Setup experience

Setup friction: MODERATE. The final clean install took 10.06 seconds; the replay took 8.05 seconds. Manual interventions: 0. Candidate network access: none.

WorthIt used pip to install the exact staged commit offline and injected a deterministic VCS-version fallback because GitHub's commit archive has no .git metadata; it did not run the README's registry command verbatim.

The commit archive lacked VCS metadata required by the build backend, so WorthIt injected the deterministic build version 0+worthit.fad14135b94e. GitHub's latest-release metadata was 8.0.1; the commit SHA, not the synthetic version, identifies the tested artifact.

## Performance

The slowest accepted test took 1781 ms. Peak measured test RAM was 36.9 MiB. No comparative baseline was run, so this is a measurement, not a speed claim.

## What broke

- No accepted final or replay test failed.
- Source installation required the recorded VCS-version fallback because the commit archive contained no .git metadata.
- Diagnostic expectations were repaired, independently criticized, and then run twice from clean containers.
- Not fully verified: CLAIM-001, Running `pip install isort` installs the isort package.
- Not fully verified: CLAIM-005, Running `isort --atomic .` runs isort against a project and only applies changes if they don't introduce syntax errors.
- Not fully verified: CLAIM-007, isort requires Python 3.10 or higher to run.

## Scorecard

| Dimension | Score | Evidence-based reason |
|---|---:|---|
| Claim Verification | 89/100 | Importance-weighted claim results; unverified claim portions reduce coverage rather than becoming failures. |
| Utility | 85/100 | 6/6 core workflow tests (T01, T02, T03, T04, T05, T07) passed. |
| Setup Experience | 75/100 | Offline source installation took 10063 ms with 0 manual interventions and 1 automated setup adjustment. |
| Reliability | 95/100 | Two clean runs compared across 7 tests; reproduced=True. |
| Performance Efficiency | 85/100 | Slowest test was 1781 ms and measured peak RAM was 38660997 bytes; no comparative baseline was used. |
| Documentation | 80/100 | README-derived workflows worked; diagnostic execution was needed to correct undocumented exit/format details. |
| Safety Privacy | 90/100 | Static screening found no high-risk behavior and candidate execution had no network; static review is not a safety guarantee. |
| Novelty | 50/100 | Neutral score: WorthIt did not run a comparative novelty study. |

## Who should use it

- Developers evaluating isort as a Python utility / library to sort imports.
- Teams that need the tested CLI workflow to run locally without candidate-time network access.

## Who should skip it

- This review is not enough if your decision requires full verification of: CLAIM-001: Running `pip install isort` installs the isort package; CLAIM-005: Running `isort --atomic .` runs isort against a project and only applies changes if they don't introduce syntax errors; CLAIM-007: isort requires Python 3.10 or higher to run.

## Reproduction and safety

Source archive SHA-256: `e7f87f6d691fd9656d746a2e8f68a4ea72e0e7ba71e609866277ad896bac852a`. The exact commit was run twice with candidate network disabled. The backend was Docker/runc with seccomp and AppArmor; runc shares the host kernel. This is not a guarantee that the repository is safe.

Structured provenance: [risk assessment](risk.json), [sandbox environment](environment.json), [dependency fetch](dependency-fetch.json), and [reproducibility comparison](reproducibility.json).

## Limitations

- T04's expected exit code of 1 for isort -c on an unsorted file is confirmed by warm evidence (observed exit 1) and matches isort's documented check-mode convention.
- Only the `isort` entrypoint was tested per plan scope; `isort-identify-imports` is left untested since no claim describes its distinct behavior.
- Tests assume no ambient isort configuration (pyproject.toml/setup.cfg/.isort.cfg) exists in the fresh case directory, so default settings govern sorting behavior.
- Tests rely on isort's default alphabetical stdlib sorting behavior (os before sys, abc before json) rather than asserting against every possible profile/config combination.
- No test was added for non-atomic in-place behavior on a file with a syntax error, since no claim documents that specific behavior; adding it would be unclaimed speculation.
- The commit archive lacked VCS metadata required by the build backend, so WorthIt injected the deterministic build version 0+worthit.fad14135b94e. GitHub's latest-release metadata was 8.0.1; the commit SHA, not the synthetic version, identifies the tested artifact.
