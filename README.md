# WorthIt

**We actually ran the thing.**

WorthIt is an execution-backed verification lab for open-source AI and developer
tools. It extracts documented claims, designs and critiques tests, installs an
exact commit in a disposable environment, runs core and failure workflows,
captures evidence, scores the result, and generates a static technical review.

This repository currently implements the first deliberately narrow track:
well-established, public Python CLI repositories with static PEP 517 metadata
and wheel-only dependencies. Daily discovery and unattended top-five execution
remain disabled until the three-repository gate is complete.

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
```

## First proof

WorthIt evaluated PyCQA/isort at commit
`fad14135b94e5600c71a2d9335555b4ad0dea2a9`. The accepted contract passed 7/7
tests twice from fresh offline containers. The resulting review is
[`reviews/pycqa/isort/fad14135b94e5600c71a2d9335555b4ad0dea2a9/review.md`](reviews/pycqa/isort/fad14135b94e5600c71a2d9335555b4ad0dea2a9/review.md).

The score is 84/100 with `HIGH` confidence. The registry-install, Python-version,
and atomic guarantees remain partial. The review records the automated synthetic
version needed because GitHub's commit archive omits VCS metadata.

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

## Design records

- [`docs/research/repository-execution-landscape.md`](docs/research/repository-execution-landscape.md)
- [`docs/v1-runner.md`](docs/v1-runner.md)

Generated run state and private diagnostic evidence live under `.worthit/` and
are ignored by Git. Only curated, redacted text evidence and validated JSON under
`reviews/` can enter the static publication.
