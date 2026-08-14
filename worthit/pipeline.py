from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .evaluate import evaluate_run
from .inspect import (
    INSPECTION_REVISION,
    assess_risk,
    inspect_repository,
    parse_repo_url,
    refresh_repository_inspection,
    resolve_commit,
)
from .models import Claim, TestPlan, TrustClass, atomic_json, jsonable, read_json
from .planning import design_and_critique_plan, extract_claims, model_cost, repair_and_critique_plan
from .review import editorial_critique, generate_review, publish_review_artifacts
from .runner import (
    DockerRunner,
    RunnerConfig,
    vcs_version_fallback,
    verify_source_snapshot,
)
from .site import build_site


def evaluate_repository(
    url: str,
    *,
    commit: str | None = None,
    work_root: Path = Path(".worthit"),
    reviews_root: Path = Path("reviews"),
    site_dir: Path = Path("_site"),
    allow_runc: bool = False,
    base_path: str = "/",
    site_url: str = "http://localhost:8000",
    run_dir_override: Path | None = None,
) -> dict[str, Any]:
    ref = parse_repo_url(url)
    sha = resolve_commit(url, commit)
    run_dir = (
        run_dir_override.resolve()
        if run_dir_override is not None
        else (work_root / "runs" / f"{ref.owner}--{ref.name}" / sha).resolve()
    )
    if (
        run_dir in {Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve()}
        or len(run_dir.parts) < 3
    ):
        raise ValueError("refusing to use a broad run directory")
    run_dir.mkdir(parents=True, exist_ok=True)
    state = _load_state(run_dir, url, sha)
    current = "inspect"
    try:
        _stage(state, run_dir, current, "RUNNING")
        repository_path = run_dir / "repository.json"
        repository = (
            json.loads(repository_path.read_text())
            if repository_path.exists()
            else inspect_repository(url, run_dir, sha)
        )
        if repository.get("commit_sha") != sha or repository.get("url") != ref.url:
            raise ValueError("cached repository identity does not match this run")
        repository = refresh_repository_inspection(repository, run_dir)
        if not (repository.get("environment") or {}).get("supported"):
            raise ValueError("repository is outside the supported V1 CLI execution tracks")
        requirements = repository.get("v1_requirements")
        if not isinstance(requirements, dict) or requirements.get("passed") is not True:
            classification = (
                requirements.get("classification") if isinstance(requirements, dict) else "MISSING"
            )
            raise ValueError(f"repository does not pass the V1 requirements gate: {classification}")
        _stage(state, run_dir, current, "COMPLETE")

        current = "risk_assess"
        _stage(state, run_dir, current, "RUNNING")
        risk_path = run_dir / "risk.json"
        # Static screening is cheap and security rules evolve; never reuse a stale verdict.
        risk_report = assess_risk(run_dir / "source", repository)
        risk = jsonable(risk_report)
        atomic_json(risk_path, risk)
        trust = TrustClass(risk["classification"])
        if trust is not TrustClass.TRUSTED:
            raise ValueError(f"V1 runner refuses risk classification {trust.value}")
        _stage(state, run_dir, current, "COMPLETE")

        current = "plan"
        _stage(state, run_dir, current, "RUNNING")
        claims_path = run_dir / "claims.json"
        if claims_path.exists():
            claims = [Claim.from_dict(item) for item in json.loads(claims_path.read_text())["claims"]]
        else:
            claims = extract_claims(repository, run_dir)
        plan_path = run_dir / "test-plan.json"
        if plan_path.exists():
            raw_plan = json.loads(plan_path.read_text())
            plan = TestPlan.from_dict(raw_plan, claims, str(raw_plan.get("designer") or "cached"))
        else:
            plan, _ = design_and_critique_plan(repository, claims, run_dir)
        _stage(state, run_dir, current, "COMPLETE")

        current = "prepare"
        _stage(state, run_dir, current, "RUNNING")
        source = run_dir / "source"
        checkout_report = verify_source_snapshot(source, run_dir / "source.tar.gz", repository)
        atomic_json(run_dir / "checkout.json", checkout_report)
        environment = repository["environment"]
        if not isinstance(environment, dict):
            raise ValueError("repository environment metadata is invalid")
        runner = DockerRunner(run_dir, RunnerConfig.from_env(allow_runc=allow_runc), trust, environment)
        runner.preflight()
        install_environment, install_adjustments = vcs_version_fallback(environment, sha)
        dependency_dir = runner.prepare_dependencies(source)
        _stage(state, run_dir, current, "COMPLETE")

        current = "diagnostic_run"
        _stage(state, run_dir, current, "RUNNING")
        warm = _execute_or_resume(
            runner, source, plan, dependency_dir, "warm", install_environment, install_adjustments
        )
        if warm.get("status") != "COMPLETE":
            raise ValueError("clean installation failed; review cannot proceed")
        _stage(state, run_dir, current, "COMPLETE")

        repaired = any(test.get("status") == "FAIL" for test in warm.get("tests", []))
        if repaired:
            current = "repair_plan"
            _stage(state, run_dir, current, "RUNNING")
            repaired_path = run_dir / "test-plan-repaired.json"
            if repaired_path.exists():
                raw_plan = json.loads(repaired_path.read_text())
                plan = TestPlan.from_dict(raw_plan, claims, str(raw_plan.get("designer") or "cached-repair"))
            else:
                plan, _ = repair_and_critique_plan(repository, claims, plan, warm, run_dir)
            _stage(state, run_dir, current, "COMPLETE")

        current = "clean_replay"
        _stage(state, run_dir, current, "RUNNING")
        final_run = _execute_or_resume(
            runner, source, plan, dependency_dir, "final", install_environment, install_adjustments
        )
        replay_run = _execute_or_resume(
            runner, source, plan, dependency_dir, "replay", install_environment, install_adjustments
        )
        _stage(state, run_dir, current, "COMPLETE")

        current = "evaluate"
        _stage(state, run_dir, current, "RUNNING")
        claim_results, score, reproducibility = evaluate_run(
            repository, claims, plan, final_run, replay_run, risk, run_dir, repaired=repaired
        )
        _stage(state, run_dir, current, "COMPLETE")

        current = "review"
        _stage(state, run_dir, current, "RUNNING")
        review, markdown, facts = generate_review(
            repository,
            claims,
            claim_results,
            plan,
            final_run,
            replay_run,
            score,
            reproducibility,
            risk,
            run_dir,
        )
        editorial = editorial_critique(review, run_dir)
        destination = publish_review_artifacts(
            review, markdown, claims, plan, final_run, score, facts, editorial, run_dir, reviews_root
        )
        _stage(state, run_dir, current, "COMPLETE")

        current = "publish_local"
        _stage(state, run_dir, current, "RUNNING")
        count = build_site(reviews_root, site_dir, base_path=base_path, site_url=site_url)
        _stage(state, run_dir, current, "COMPLETE")
        result = {
            "run_dir": str(run_dir),
            "review_dir": str(destination.resolve()),
            "site_dir": str(site_dir.resolve()),
            "site_reviews": count,
            "score": score.overall,
            "confidence": score.confidence.value,
            "verdict": score.verdict,
            "llm_cost_usd": model_cost(run_dir),
        }
        atomic_json(run_dir / "result.json", result)
        return result
    except BaseException as error:
        _stage(state, run_dir, current, "FAILED", str(error))
        raise


def _execute_or_resume(
    runner: DockerRunner,
    source: Path,
    plan: TestPlan,
    dependency_dir: Path,
    label: str,
    install_environment: dict[str, str],
    install_adjustments: list[dict[str, str]],
) -> dict[str, Any]:
    path = runner.run_dir / f"{label}.json"
    if path.exists():
        cached = read_json(path)
        if cached.get("execution_contract_sha256") == runner.execution_contract_sha256(
            plan, install_environment
        ):
            return cached
    executed = jsonable(
        runner.execute_plan(source, plan, dependency_dir, label, install_environment, install_adjustments)
    )
    if not isinstance(executed, dict):
        raise TypeError("runner result was not an object")
    return executed


def _load_state(run_dir: Path, url: str, sha: str) -> dict[str, Any]:
    path = run_dir / "state.json"
    if path.exists():
        state = read_json(path)
        if state.get("repository_url") != url or state.get("commit_sha") != sha:
            raise ValueError("run state identity mismatch")
        if state.get("inspection_revision") == INSPECTION_REVISION:
            return state
        old_revision = state.get("inspection_revision")
        old_revision = old_revision if isinstance(old_revision, int) and old_revision >= 0 else 0
        archive = run_dir / f"stale-inspection-{old_revision}"
        suffix = 1
        while archive.exists() or archive.is_symlink():
            archive = run_dir / f"stale-inspection-{old_revision}-{suffix}"
            suffix += 1
        archive.mkdir()
        for child in list(run_dir.iterdir()):
            if child.name in {
                "source",
                "source.tar.gz",
                "repository.json",
                archive.name,
            } or child.name.startswith("stale-inspection-"):
                continue
            child.rename(archive / child.name)
    state = {
        "repository_url": url,
        "commit_sha": sha,
        "inspection_revision": INSPECTION_REVISION,
        "stages": {},
    }
    atomic_json(path, state)
    return state


def _stage(state: dict[str, Any], run_dir: Path, name: str, status: str, error: str | None = None) -> None:
    state["stages"][name] = {
        "status": status,
        "updated_at": datetime.now(UTC).isoformat(),
        "error": error,
    }
    atomic_json(run_dir / "state.json", state)
