# WorthIt

**We actually ran the thing.**

WorthIt is an execution-backed verification lab for open-source AI and developer
tools. It extracts documented claims, designs and critiques tests, installs an
exact commit in a disposable environment, runs core and failure workflows,
captures evidence, scores the result, and generates a static technical review.

This repository implements deliberately narrow Python, Node, and Go CLI tracks.
Python requires static PEP 517 metadata and wheel dependencies; Node requires a
declared CLI with no runtime dependencies or lifecycle scripts; Go requires a
root command with pinned modules. All three tracks have passed the clean-replay
gate. Daily discovery now ranks a persistent backlog and selects at most five
repositories that pass every gate. It is allowed to select none.

The generated publication is deployed at
[drj0e.github.io/worthit](https://drj0e.github.io/worthit/).

## Run one evaluation

Requirements: Python 3.12+, Docker, and an authenticated Claude CLI. Read
[`docs/v1-runner.md`](docs/v1-runner.md) before acknowledging the current runc
backend.

```bash
python -m worthit evaluate https://github.com/OWNER/REPOSITORY --allow-runc
python -m http.server --directory _site
```

`--allow-runc` is intentionally explicit. The local backend uses non-root,
capability-free, network-disabled containers with no host mounts or inherited
credentials, but rootful runc still shares the host kernel. V1 therefore refuses
anything below `TRUSTED_ENOUGH_TO_TEST`.

Rebuild the static publication without running candidate code:

```bash
python -m worthit build-site
python -m worthit verify-site
```

Run the inexpensive daily discovery and qualification pass:

```bash
python -m worthit daily
```

Execution remains an explicit risk acknowledgement and obeys per-repository and
daily model-cost ceilings:

```bash
python -m worthit daily --execute --allow-runc
```

## First proof

WorthIt evaluated PyCQA/isort at commit
`fad14135b94e5600c71a2d9335555b4ad0dea2a9`. Its finalized contract passed 8/8
tests twice from fresh offline containers, including the documented Python API
workflow added after independent plan critique. The resulting review is
[`reviews/pycqa/isort/fad14135b94e5600c71a2d9335555b4ad0dea2a9/review.md`](reviews/pycqa/isort/fad14135b94e5600c71a2d9335555b4ad0dea2a9/review.md).

The score is 84/100 with `MEDIUM` confidence. The registry-install,
Python-version, and multi-file guarantees remain partial. The review records the
automated synthetic version needed because GitHub's commit archive omits VCS
metadata.

The second proof evaluated mishoo/UglifyJS at commit
`111746bbae5f55c88e3b82b42f14fd0f3129ea53`. Its accepted Node contract passed
6/6 tests in both clean offline containers. The evidence-backed review is
[`reviews/mishoo/uglifyjs/111746bbae5f55c88e3b82b42f14fd0f3129ea53/review.md`](reviews/mishoo/uglifyjs/111746bbae5f55c88e3b82b42f14fd0f3129ea53/review.md).

The third proof evaluated tomnomnom/gron at commit
`88a6234ea2d0c487090988182ad9a7cdf6def924`. Its pinned Go module graph built
offline and 7/7 JSON, stream, reverse-conversion, filtering, and invalid-input
tests passed in both clean containers. The evidence-backed review is
[`reviews/tomnomnom/gron/88a6234ea2d0c487090988182ad9a7cdf6def924/review.md`](reviews/tomnomnom/gron/88a6234ea2d0c487090988182ad9a7cdf6def924/review.md).

## Checks

```bash
python -m unittest discover -s tests -v
WORTHIT_DOCKER_TESTS=1 python -m unittest tests.test_core.DockerIsolationTests -v
ruff check .
ruff format --check .
mypy worthit
```

The opt-in Docker test verifies the actual container has no host mounts,
inherited canary secret, Docker socket, host home, or network, and confirms
timeout teardown.

GitHub Actions keeps candidate execution and Pages credentials in separate jobs.
The scheduled discovery report runs without a model credential. Adding either
the `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` repository secret activates
the already-configured evaluation step.

## Design records

- [`docs/research/repository-execution-landscape.md`](docs/research/repository-execution-landscape.md)
- [`docs/v1-runner.md`](docs/v1-runner.md)

Generated run state and private diagnostic evidence live under `.worthit/` and
are ignored by Git. Only curated, redacted text evidence and validated JSON under
`reviews/`, plus hash-bound append-only corrections under `corrections/`, can
enter the static publication.
