# starlette: WorthIt verification review

- Repository: [encode/starlette](https://github.com/encode/starlette)
- Commit tested: `398e5a3430eb1ddd33e1d48d766efe41426e231f`
- Tool version output: [`1.6.0`](evidence/final/version/stdout.txt)
- Date tested: 2026-08-14
- WorthIt version: `0.2.0`
- Category: Python library
- WorthIt Score: 81/100
- Confidence: MEDIUM
- Verdict: WORTH IT
- Bullshit Ratio: 0%

## TL;DR

The exact commit completed pip library source install from an offline wheelhouse in 8.04 seconds with no manual intervention. 7 of 7 accepted tests passed with the same results in a second clean, offline container. Claim coverage gaps: 5 partial, 1 unverified. Evidence confidence is medium.

## Claim matrix

### CLAIM-001: PARTIAL

> Starlette can be installed via `pip install starlette`.

Source: `README.md`. Tests: T01. The exact commit completed pip library source install from an offline wheelhouse, but the documented registry command was not run verbatim.

### CLAIM-002: PARTIAL

> Running a Starlette application requires a separate ASGI server (e.g. uvicorn), which is not bundled with the `starlette` package itself.

Source: `README.md`. Tests: T04. Tests T04 passed in both clean replays. Remaining gap: CLAIM-002 and CLAIM-003 (optional dependency behavior) are verified via installed package metadata (importlib.metadata.requires) rather than by actually installing/uninstalling httpx2, jinja2, python-multipart, itsdangerous, or pyyaml and exercising TestClient/Jinja2Templates/SessionMiddleware/SchemaGenerator failure paths, since installing extras requires network access.

### CLAIM-003: PARTIAL

> `anyio` is the only hard runtime dependency of Starlette; `httpx2`, `jinja2`, `python-multipart`, `itsdangerous`, and `pyyaml` are optional and only required for specific features (TestClient, Jinja2Templates, form parsing, SessionMiddleware, and SchemaGenerator respectively).

Source: `README.md`. Tests: T05. Tests T05 passed in both clean replays. Remaining gap: CLAIM-002 and CLAIM-003 (optional dependency behavior) are verified via installed package metadata (importlib.metadata.requires) rather than by actually installing/uninstalling httpx2, jinja2, python-multipart, itsdangerous, or pyyaml and exercising TestClient/Jinja2Templates/SessionMiddleware/SchemaGenerator failure paths, since installing extras requires network access.

### CLAIM-004: UNVERIFIED

> Running `pip install starlette[full]` installs all of Starlette's optional dependencies (httpx2, jinja2, python-multipart, itsdangerous, pyyaml).

Source: `README.md`. Tests: installation stage / no direct test. No accepted test exercised this claim.

### CLAIM-005: PARTIAL

> The documented example, defining a homepage endpoint returning `JSONResponse({'hello': 'world'})` registered via `Route("/", endpoint=homepage)` and wrapped in `Starlette(debug=True, routes=routes)`, runs successfully with `uvicorn main:app` and serves that JSON response at `/`.

Source: `README.md`. Tests: T02, T07. Tests T02, T07 passed in both clean replays. Remaining gap: Tests avoid uvicorn and TestClient/httpx entirely since only the exact staged starlette commit is pip-installed (no network, no extras); the ASGI protocol is exercised directly via hand-written scope/receive/send instead of a real HTTP client or server socket, so CLAIM-005 and CLAIM-006's literal 'run with uvicorn' and 'request over HTTP' framing is verified only at the ASGI-callable level, not end-to-end over a socket.

### CLAIM-006: PARTIAL

> Starlette's `PlainTextResponse` component can be used independently as a raw ASGI callable (without the `Starlette` application class) to serve 'Hello, world!' when run with `uvicorn example:app`.

Source: `README.md`. Tests: T03, T06. Tests T03, T06 passed in both clean replays. Remaining gap: Tests avoid uvicorn and TestClient/httpx entirely since only the exact staged starlette commit is pip-installed (no network, no extras); the ASGI protocol is exercised directly via hand-written scope/receive/send instead of a real HTTP client or server socket, so CLAIM-005 and CLAIM-006's literal 'run with uvicorn' and 'request over HTTP' framing is verified only at the ASGI-callable level, not end-to-end over a socket.

## What we ran

- T01 (core): PASS, exit 0, 1768 ms, [stdout](evidence/final/T01/stdout.txt), [stderr](evidence/final/T01/stderr.txt). Confirm the starlette package installed from the staged commit imports successfully.
- T02 (core): PASS, exit 0, 1360 ms, [stdout](evidence/final/T02/stdout.txt), [stderr](evidence/final/T02/stderr.txt). Reproduce the README homepage example (Starlette app + Route + JSONResponse) and confirm it returns status 200 with a JSON body that decodes to {'hello': 'world'} when driven through the ASGI protocol directly. The body is parsed with json.loads and compared as a dict rather than asserting the exact serialized byte string, since the README only documents the dict value, not JSONResponse's internal separator/formatting choices.
- T03 (core): PASS, exit 0, 1284 ms, [stdout](evidence/final/T03/stdout.txt), [stderr](evidence/final/T03/stderr.txt). Reproduce the README's toolkit example: PlainTextResponse used as a bare ASGI callable independent of the Starlette application class, and confirm it serves 'Hello, world!' with status 200.
- T04 (core): PASS, exit 0, 1286 ms, [stdout](evidence/final/T04/stdout.txt), [stderr](evidence/final/T04/stderr.txt). Confirm uvicorn is not an unconditional Starlette dependency. Requirements tagged with an 'extra ==' marker are excluded; this does not test every possible ASGI server package.
- T05 (core): PASS, exit 0, 1289 ms, [stdout](evidence/final/T05/stdout.txt), [stderr](evidence/final/T05/stderr.txt). Confirm anyio is declared as an unconditional (hard) requirement while jinja2 is declared only as an optional extra, per the documented dependency split.
- T06 (edge/failure): PASS, exit 1, 1361 ms, [stdout](evidence/final/T06/stdout.txt), [stderr](evidence/final/T06/stderr.txt). Failure case: the documented bare-ASGI-callable example explicitly asserts scope['type'] == 'http'; feeding a non-http scope (e.g. websocket) must raise an uncaught AssertionError, confirming the exact code sample as written behaves this way. This validates fidelity to the literal README code sample, not a separately documented Starlette guarantee about non-http scope handling.
- T07 (edge/failure): PASS, exit 0, 1461 ms, [stdout](evidence/final/T07/stdout.txt), [stderr](evidence/final/T07/stderr.txt). Edge case: requesting a path not registered in the README's single-route example returns a non-200 status through the ASGI router. This is a supplementary smoke check on routing behavior surrounding CLAIM-005's example, not itself a documented claim about 404 handling.

## Setup experience

Setup friction: EASY. The final clean install took 8.04 seconds; the replay took 8.05 seconds. Manual interventions: 0. Candidate network access: none.

WorthIt used pip to install the exact staged library commit offline, then exercised its documented API through isolated user scripts; it did not run the project's own test suite.

The source install reported 1.6.0, matching GitHub's latest-release metadata 1.6.0. The commit SHA identifies the exact tested artifact.

## Performance

The slowest accepted test took 1768 ms. Peak measured test RAM was 44.4 MiB. No comparative baseline was run, so this is a measurement, not a speed claim.

## What broke

- No accepted final or replay test failed.
- The accepted contract did not require diagnostic repair.
- Not fully verified: CLAIM-001, Starlette can be installed via `pip install starlette`.
- Not fully verified: CLAIM-002, Running a Starlette application requires a separate ASGI server (e.g. uvicorn), which is not bundled with the `starlette` package itself.
- Not fully verified: CLAIM-003, `anyio` is the only hard runtime dependency of Starlette; `httpx2`, `jinja2`, `python-multipart`, `itsdangerous`, and `pyyaml` are optional and only required for specific features (TestClient, Jinja2Templates, form parsing, SessionMiddleware, and SchemaGenerator respectively).
- Not fully verified: CLAIM-005, The documented example, defining a homepage endpoint returning `JSONResponse({'hello': 'world'})` registered via `Route("/", endpoint=homepage)` and wrapped in `Starlette(debug=True, routes=routes)`, runs successfully with `uvicorn main:app` and serves that JSON response at `/`.
- Not fully verified: CLAIM-006, Starlette's `PlainTextResponse` component can be used independently as a raw ASGI callable (without the `Starlette` application class) to serve 'Hello, world!' when run with `uvicorn example:app`.
- Unverified: CLAIM-004, Running `pip install starlette[full]` installs all of Starlette's optional dependencies (httpx2, jinja2, python-multipart, itsdangerous, pyyaml).

## Scorecard

| Dimension | Score | Evidence-based reason |
|---|---:|---|
| Claim Verification | 70/100 | Importance-weighted claim results; unverified claim portions reduce coverage rather than becoming failures. |
| Utility | 85/100 | 5/5 core workflow tests (T01, T02, T03, T04, T05) passed. |
| Setup Experience | 90/100 | pip library source install from an offline wheelhouse took 8044 ms with 0 manual interventions and 0 automated setup adjustments. |
| Reliability | 95/100 | Two clean runs compared across 7 tests; reproduced=True. |
| Performance Efficiency | 85/100 | Slowest test was 1768 ms and measured peak RAM was 46546289 bytes; no comparative baseline was used. |
| Documentation | 90/100 | README-derived workflows worked without contract repair. |
| Safety Privacy | 90/100 | Static screening recorded no findings; candidate execution had no network. Static review is not a safety guarantee. |
| Novelty | 50/100 | Neutral score: WorthIt did not run a comparative novelty study. |

## Who should use it

- Developers evaluating starlette's tested Python library API.
- Teams that need the tested workflow to run locally without candidate-time network access.

## Who should skip it

- This review is not enough if your decision requires full verification of: CLAIM-001: Starlette can be installed via `pip install starlette`; CLAIM-002: Running a Starlette application requires a separate ASGI server (e.g. uvicorn), which is not bundled with the `starlette` package itself; CLAIM-003: `anyio` is the only hard runtime dependency of Starlette; `httpx2`, `jinja2`, `python-multipart`, `itsdangerous`, and `pyyaml` are optional and only required for specific features (TestClient, Jinja2Templates, form parsing, SessionMiddleware, and SchemaGenerator respectively); CLAIM-005: The documented example, defining a homepage endpoint returning `JSONResponse({'hello': 'world'})` registered via `Route("/", endpoint=homepage)` and wrapped in `Starlette(debug=True, routes=routes)`, runs successfully with `uvicorn main:app` and serves that JSON response at `/`; CLAIM-006: Starlette's `PlainTextResponse` component can be used independently as a raw ASGI callable (without the `Starlette` application class) to serve 'Hello, world!' when run with `uvicorn example:app`; CLAIM-004: Running `pip install starlette[full]` installs all of Starlette's optional dependencies (httpx2, jinja2, python-multipart, itsdangerous, pyyaml).

## Reproduction and safety

Source archive SHA-256: `e2c2c835e016a75faa5bcf1364a4bf2aa9f829de3f3f875d0a4132d754265168`. The exact commit was run twice with candidate network disabled. The backend was docker/runc with seccomp and AppArmor; runc shares the host kernel. This is not a guarantee that the repository is safe.

Structured provenance: [final run](run.json), [replay run](replay.json), [risk assessment](risk.json), [sandbox environment](environment.json), [dependency fetch](dependency-fetch.json), and [reproducibility comparison](reproducibility.json).

## Limitations

- CLAIM-004 (pip install starlette[full]) is not tested at all because it requires a real package index installation, which is disallowed in this offline/no-network test harness.
- T02 parses the JSON response body with json.loads and compares it as a dict to {'hello': 'world'} rather than asserting the exact serialized byte string, so it tracks only the dict value documented by the README and is insensitive to JSONResponse's internal separator/formatting choices, which are not claimed.
- The source install reported 1.6.0, matching GitHub's latest-release metadata 1.6.0. The commit SHA identifies the exact tested artifact.
