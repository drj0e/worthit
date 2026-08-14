# UglifyJS: WorthIt verification review

- Repository: [mishoo/UglifyJS](https://github.com/mishoo/UglifyJS)
- Commit tested: `111746bbae5f55c88e3b82b42f14fd0f3129ea53`
- Tool version output: [`uglify-js 3.19.3`](evidence/final/version/stdout.txt)
- Date tested: 2026-08-14
- Category: CLI utility
- WorthIt Score: 87/100
- Confidence: HIGH
- Verdict: WORTH IT
- Bullshit Ratio: 0%

## TL;DR

The exact commit completed npm global source install with lifecycle scripts disabled in 2.01 seconds with no manual intervention. 6 of 6 accepted tests passed with the same results in a second clean, offline container. Claim coverage gaps: 2 partial, 1 unverified. Evidence confidence is high.

## Claim matrix

### CLAIM-001: PARTIAL

> Running `npm install uglify-js -g` installs UglifyJS as a command line app.

Source: `README.md`. Tests: installation stage / no direct test. The exact commit completed npm global source install with lifecycle scripts disabled, but the documented registry command was not run verbatim.

### CLAIM-002: PASS

> After installation, the CLI is invoked as `uglifyjs [input files] [options]`.

Source: `README.md`. Tests: T01, T06. All mapped tests passed in both clean replays.

### CLAIM-003: PASS

> If no input file is given on the command line, UglifyJS reads JavaScript source from STDIN.

Source: `README.md`. Tests: T02. All mapped tests passed in both clean replays.

### CLAIM-004: PASS

> UglifyJS can accept multiple input files, parses them in sequence within the same global scope, and resolves cross-file variable/function references correctly.

Source: `README.md`. Tests: T05. All mapped tests passed in both clean replays.

### CLAIM-005: PARTIAL

> Without the `--output`/`-o` flag, UglifyJS writes its minified output to STDOUT; with it, output is written to the specified file.

Source: `README.md`. Tests: T03. Tests T03 passed in both clean replays. Remaining gap: T03 and T04 verify that output is correctly written to the specified file but cannot assert that STDOUT is empty in this case, because the test schema only supports positive substring matching (stdout_contains) with no exact/empty-stdout assertion mechanism; the 'no STDOUT when -o is used' half of CLAIM-005 is therefore not fully verified.

### CLAIM-006: PASS

> Passing `--source-map --output output.js` causes UglifyJS to write a source map file to `output.js.map`.

Source: `README.md`. Tests: T04. All mapped tests passed in both clean replays.

### CLAIM-007: UNVERIFIED

> The package declares a required Node.js engine version of >=0.8.0 for uglify-js.

Source: `package.json`. Tests: installation stage / no direct test. No accepted test exercised this claim.

### CLAIM-008: PASS

> The package exposes a binary named `uglifyjs` located at `bin/uglifyjs`, which becomes the CLI entry point after installation.

Source: `package.json`. Tests: T01. All mapped tests passed in both clean replays.

## What we ran

- T01 (core): PASS, exit 0, 1751 ms, [stdout](evidence/final/T01/stdout.txt), [stderr](evidence/final/T01/stderr.txt). Verify the installed uglifyjs binary accepts an input file plus a compress option and prints compressed code to STDOUT, without relying on mangler-specific variable naming.
- T02 (core): PASS, exit 0, 1773 ms, [stdout](evidence/final/T02/stdout.txt), [stderr](evidence/final/T02/stderr.txt). Verify uglifyjs reads JavaScript from STDIN and compresses it when no input file is given.
- T03 (core): PASS, exit 0, 1773 ms, [stdout](evidence/final/T03/stdout.txt), [stderr](evidence/final/T03/stderr.txt). Verify that with -o/--output specified, compressed output is written to the named file with the expected content.
- T04 (core): PASS, exit 0, 1468 ms, [stdout](evidence/final/T04/stdout.txt), [stderr](evidence/final/T04/stderr.txt). Verify that passing --source-map with --output output.js causes UglifyJS to generate a source map file named output.js.map alongside the compressed output file, and that the map contains the standard source-map fields.
- T05 (core): PASS, exit 0, 1285 ms, [stdout](evidence/final/T05/stdout.txt), [stderr](evidence/final/T05/stderr.txt). Verify multiple input files are parsed in sequence within the same global scope so a reference in file2.js to a function declared in file1.js resolves correctly. With -c and --toplevel enabled, correct cross-file linkage lets the compressor constant-fold add(1+2,3+4) into its computed result, which is only possible if add's definition from file1.js was correctly resolved while processing the call in file2.js.
- T06 (edge/failure): PASS, exit 1, 1777 ms, [stdout](evidence/final/T06/stdout.txt), [stderr](evidence/final/T06/stderr.txt). Verify uglifyjs fails with a nonzero exit code when given syntactically invalid JavaScript, as a minimal edge-case check; exact exit code and stderr wording are not documented so are not asserted.

## Setup experience

Setup friction: EASY. The final clean install took 2.01 seconds; the replay took 2.01 seconds. Manual interventions: 0. Candidate network access: none.

WorthIt used npm to install the exact staged commit globally with network and lifecycle scripts disabled; it did not run the README's registry command verbatim.

The source install reported uglify-js 3.19.3, matching GitHub's latest-release metadata v3.19.3. The commit SHA identifies the exact tested artifact.

## Performance

The slowest accepted test took 1777 ms. Peak measured test RAM was 10.2 MiB. No comparative baseline was run, so this is a measurement, not a speed claim.

## What broke

- No accepted final or replay test failed.
- Diagnostic expectations were repaired, independently criticized, and then run twice from clean containers.
- Not fully verified: CLAIM-001, Running `npm install uglify-js -g` installs UglifyJS as a command line app.
- Not fully verified: CLAIM-005, Without the `--output`/`-o` flag, UglifyJS writes its minified output to STDOUT; with it, output is written to the specified file.
- Unverified: CLAIM-007, The package declares a required Node.js engine version of >=0.8.0 for uglify-js.
- Static screening recorded MEDIUM privilege_escalation at .github/workflows/ci.yml; see risk.json for context.

## Scorecard

| Dimension | Score | Evidence-based reason |
|---|---:|---|
| Claim Verification | 91/100 | Importance-weighted claim results; unverified claim portions reduce coverage rather than becoming failures. |
| Utility | 85/100 | 5/5 core workflow tests (T01, T02, T03, T04, T05) passed. |
| Setup Experience | 90/100 | npm global source install with lifecycle scripts disabled took 2012 ms with 0 manual interventions and 0 automated setup adjustments. |
| Reliability | 95/100 | Two clean runs compared across 6 tests; reproduced=True. |
| Performance Efficiency | 85/100 | Slowest test was 1777 ms and measured peak RAM was 10695475 bytes; no comparative baseline was used. |
| Documentation | 80/100 | README-derived workflows worked; diagnostic execution was needed to correct undocumented exit/format details. |
| Safety Privacy | 90/100 | Static screening recorded 1 MEDIUM finding(s); candidate execution had no network. Static review is not a safety guarantee. |
| Novelty | 50/100 | Neutral score: WorthIt did not run a comparative novelty study. |

## Who should use it

- Developers evaluating UglifyJS as javaScript parser / mangler / compressor / beautifier toolkit.
- Teams that need the tested CLI workflow to run locally without candidate-time network access.

## Who should skip it

- This review is not enough if your decision requires full verification of: CLAIM-001: Running `npm install uglify-js -g` installs UglifyJS as a command line app; CLAIM-005: Without the `--output`/`-o` flag, UglifyJS writes its minified output to STDOUT; with it, output is written to the specified file; CLAIM-007: The package declares a required Node.js engine version of >=0.8.0 for uglify-js.

## Reproduction and safety

Source archive SHA-256: `0bcdc7aa9cb59330cc79b1e553f22385182d47d89711a9f42144ffd5761d07e9`. The exact commit was run twice with candidate network disabled. The backend was docker/runc with seccomp and AppArmor; runc shares the host kernel. This is not a guarantee that the repository is safe.

Structured provenance: [risk assessment](risk.json), [sandbox environment](environment.json), [dependency fetch](dependency-fetch.json), and [reproducibility comparison](reproducibility.json).

## Limitations

- CLAIM-007 (engines >=0.8.0 in package.json) is not tested since it would require running against multiple Node.js versions, which is outside the scope of behavioral CLI tests.
- T01/T02/T03/T05 use compress-only (-c, no -m) output instead of the mangled-identifier string from the README's programmatic API example, since mangler variable-naming can vary across versions/flag combinations; compress-only whitespace/brace stripping is more stable but is still an assumption about the compressor's exact formatting conventions.
- T04 does not assert the presence of a `//# sourceMappingURL=output.js.map` comment in output.js: the claim text only promises that a source map file named output.js.map is written, not that a linking comment is embedded in the compressed output, so that assertion is out of scope for this claim and is not included.
- T05 asserts the constant-folded numeric result (console.log(10)) rather than an intermediate partially-evaluated call, since with -c and --toplevel the compressor fully inlines and folds the cross-file call; this is a stronger indicator that the reference to add() declared in file1.js was correctly resolved while compressing file2.js, since an unresolved or incorrectly scoped reference could not have produced the correct folded value. This does couple the test to the compressor's current folding/inlining aggressiveness, and could produce a false negative if a future version changes folding behavior for reasons unrelated to cross-file scope resolution.
- T06's exit code and stderr content on parse failure are not documented anywhere in the claims; the test only asserts that a nonzero exit occurs (from a small set of plausible codes) and makes no assertion about stderr text, to avoid a misleading FAIL on undocumented behavior.
- Static screening recorded MEDIUM privilege_escalation at .github/workflows/ci.yml; see risk.json for context.
- The source install reported uglify-js 3.19.3, matching GitHub's latest-release metadata v3.19.3. The commit SHA identifies the exact tested artifact.
