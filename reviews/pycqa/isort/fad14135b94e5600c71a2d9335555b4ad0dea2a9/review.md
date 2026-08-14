# isort: WorthIt verification review

- Repository: [PyCQA/isort](https://github.com/PyCQA/isort)
- Commit tested: `fad14135b94e5600c71a2d9335555b4ad0dea2a9`
- Tool version output: [`0+worthit.fad14135b94e`](evidence/final/version/stdout.txt)
- Date tested: 2026-08-14
- WorthIt version: `0.1.0`
- Category: CLI utility
- WorthIt Score: 84/100
- Confidence: MEDIUM
- Verdict: WORTH IT
- Bullshit Ratio: 0%

## TL;DR

The exact commit completed pip source install from an offline wheelhouse in 8.05 seconds with no manual intervention and 1 automated VCS-version adjustment. 8 of 8 accepted tests passed with the same results in a second clean, offline container. Claim coverage gaps: 3 partial, 0 unverified. Evidence confidence is medium.

## Claim matrix

### CLAIM-001: PARTIAL

> isort can be installed by running `pip install isort`.

Source: `README.md`. Tests: T01. The exact commit completed pip source install from an offline wheelhouse, but the documented registry command was not run verbatim. An automated VCS-version fallback was required.

### CLAIM-002: PARTIAL

> isort requires Python 3.10 or higher to run.

Source: `README.md`. Tests: T08. Tests T08 passed in both clean replays. Remaining gap: CLAIM-002 is verified only via consistency with packaging metadata (T08 confirms Requires-Python is >=3.10.0, matching the README's stated floor); no test actually runs isort under an unsupported (<3.10) Python interpreter to confirm the floor is enforced at runtime, since only one interpreter is available in the harness.

### CLAIM-003: PARTIAL

> Running `isort mypythonfile.py mypythonfile2.py` from the command line sorts the imports in the specified files.

Source: `README.md`. Tests: T01, T02. Tests T01, T02 passed in both clean replays. Remaining gap: The original argparse unrecognized-flag test (unclaimed CLI error-handling behavior) was removed: it was not grounded in any documented claim and risked misattributing failures to CLAIM-003 if isort's argument-parsing internals changed while the documented sorting behavior remained intact.

### CLAIM-004: PASS

> Running `isort .` applies import sorting recursively to all Python files in the current directory.

Source: `README.md`. Tests: T03. All mapped tests passed in both clean replays.

### CLAIM-005: PASS

> Running `isort mypythonfile.py --diff` shows proposed import-sorting changes without modifying the file.

Source: `README.md`. Tests: T04. All mapped tests passed in both clean replays.

### CLAIM-006: PASS

> Running isort with the `-c` (check-only) option verifies whether code has correctly sorted imports, printing incorrectly sorted files to stderr instead of modifying them.

Source: `README.md`. Tests: T05, T06. All mapped tests passed in both clean replays.

### CLAIM-007: PASS

> isort can be used as a Python library by calling `isort.file("pythonfile.py")` to sort imports in a file.

Source: `README.md`. Tests: T07. All mapped tests passed in both clean replays.

### CLAIM-008: PASS

> The isort package declares a minimum required Python version of 3.10.0 in its packaging metadata.

Source: `pyproject.toml`. Tests: T08. All mapped tests passed in both clean replays.

## What we ran

- T01 (core): PASS, exit 0, 1777 ms, [stdout](evidence/final/T01/stdout.txt), [stderr](evidence/final/T01/stderr.txt). Verify isort sorts imports alphabetically in a single specified file, confirming the core documented CLI workflow and that the pip-installed entrypoint runs successfully.
- T02 (core): PASS, exit 0, 1283 ms, [stdout](evidence/final/T02/stdout.txt), [stderr](evidence/final/T02/stderr.txt). Verify isort sorts imports independently across multiple files passed as separate arguments in a single invocation.
- T03 (core): PASS, exit 0, 1362 ms, [stdout](evidence/final/T03/stdout.txt), [stderr](evidence/final/T03/stderr.txt). Verify isort . recursively sorts imports in all Python files under the current directory, including nested subdirectories.
- T04 (core): PASS, exit 0, 1411 ms, [stdout](evidence/final/T04/stdout.txt), [stderr](evidence/final/T04/stderr.txt). Verify isort --diff prints the proposed reordering to stdout without modifying the target file on disk.
- T05 (edge/failure): PASS, exit 1, 1312 ms, [stdout](evidence/final/T05/stdout.txt), [stderr](evidence/final/T05/stderr.txt). Realistic failure case: verify isort -c (check-only) reports an incorrectly sorted file as an error to stderr with a nonzero exit code, and leaves the file unmodified.
- T06 (core): PASS, exit 0, 1372 ms, [stdout](evidence/final/T06/stdout.txt), [stderr](evidence/final/T06/stderr.txt). Verify isort -c succeeds with exit code 0 and no error output when the file's imports are already correctly sorted, and leaves the file unmodified.
- T07 (core): PASS, exit 0, 1435 ms, [stdout](evidence/final/T07/stdout.txt), [stderr](evidence/final/T07/stderr.txt). Verify the documented Python library API: importing isort and calling isort.file() sorts the imports of the target file on disk.
- T08 (core): PASS, exit 0, 1283 ms, [stdout](evidence/final/T08/stdout.txt), [stderr](evidence/final/T08/stderr.txt). Verify the installed package's declared minimum Python version (from packaging metadata) matches the README's stated 3.10+ requirement.

## Setup experience

Setup friction: MODERATE. The final clean install took 8.05 seconds; the replay took 8.05 seconds. Manual interventions: 0. Candidate network access: none.

WorthIt used pip to install the exact staged commit offline and injected a deterministic VCS-version fallback because GitHub's commit archive has no .git metadata; it did not run the README's registry command verbatim.

The commit archive lacked VCS metadata required by the build backend, so WorthIt injected the deterministic build version 0+worthit.fad14135b94e. GitHub's latest-release metadata was 8.0.1; the commit SHA, not the synthetic version, identifies the tested artifact.

## Performance

The slowest accepted test took 1777 ms. Peak measured test RAM was 36.6 MiB. No comparative baseline was run, so this is a measurement, not a speed claim.

## What broke

- No accepted final or replay test failed.
- Source installation required the recorded VCS-version fallback because the commit archive contained no .git metadata.
- The accepted contract did not require diagnostic repair.
- Not fully verified: CLAIM-001, isort can be installed by running `pip install isort`.
- Not fully verified: CLAIM-002, isort requires Python 3.10 or higher to run.
- Not fully verified: CLAIM-003, Running `isort mypythonfile.py mypythonfile2.py` from the command line sorts the imports in the specified files.

## Scorecard

| Dimension | Score | Evidence-based reason |
|---|---:|---|
| Claim Verification | 86/100 | Importance-weighted claim results; unverified claim portions reduce coverage rather than becoming failures. |
| Utility | 85/100 | 7/7 core workflow tests (T01, T02, T03, T04, T06, T07, T08) passed. |
| Setup Experience | 75/100 | pip source install from an offline wheelhouse took 8050 ms with 0 manual interventions and 1 automated setup adjustment. |
| Reliability | 95/100 | Two clean runs compared across 8 tests; reproduced=True. |
| Performance Efficiency | 85/100 | Slowest test was 1777 ms and measured peak RAM was 38325453 bytes; no comparative baseline was used. |
| Documentation | 90/100 | README-derived workflows worked without contract repair. |
| Safety Privacy | 90/100 | Static screening recorded no findings; candidate execution had no network. Static review is not a safety guarantee. |
| Novelty | 50/100 | Neutral score: WorthIt did not run a comparative novelty study. |

## Who should use it

- Developers evaluating isort as a Python utility / library to sort imports.
- Teams that need the tested CLI workflow to run locally without candidate-time network access.

## Who should skip it

- This review is not enough if your decision requires full verification of: CLAIM-001: isort can be installed by running `pip install isort`; CLAIM-002: isort requires Python 3.10 or higher to run; CLAIM-003: Running `isort mypythonfile.py mypythonfile2.py` from the command line sorts the imports in the specified files.

## Reproduction and safety

Source archive SHA-256: `e7f87f6d691fd9656d746a2e8f68a4ea72e0e7ba71e609866277ad896bac852a`. The exact commit was run twice with candidate network disabled. The backend was docker/runc with seccomp and AppArmor; runc shares the host kernel. This is not a guarantee that the repository is safe.

Structured provenance: [final run](run.json), [replay run](replay.json), [risk assessment](risk.json), [sandbox environment](environment.json), [dependency fetch](dependency-fetch.json), and [reproducibility comparison](reproducibility.json).

## Limitations

- Exact stdout wording for 'Fixing <file>' and diff header formatting is based on documented/typical isort CLI behavior; minor cosmetic wording differences across isort versions are tolerated via substring (contains) matching rather than exact text matching for stdout/stderr.
- The other declared console script, isort-identify-imports, is intentionally left untested per this plan's scope.
- The commit archive lacked VCS metadata required by the build backend, so WorthIt injected the deterministic build version 0+worthit.fad14135b94e. GitHub's latest-release metadata was 8.0.1; the commit SHA, not the synthetic version, identifies the tested artifact.
