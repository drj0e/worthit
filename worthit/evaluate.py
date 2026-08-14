from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import (
    Claim,
    ClaimResult,
    Confidence,
    Importance,
    ResultState,
    Scorecard,
    TestPlan,
    atomic_json,
)

WEIGHTS = {
    "claim_verification": 30,
    "utility": 20,
    "setup_experience": 15,
    "reliability": 10,
    "performance_efficiency": 10,
    "documentation": 5,
    "safety_privacy": 5,
    "novelty": 5,
}
IMPORTANCE_WEIGHT = {Importance.HIGH: 3, Importance.MEDIUM: 2, Importance.LOW: 1}


class ReproducibilityHold(ValueError):
    outcome_status = "HOLD_INSUFFICIENT_EVIDENCE"


def evaluate_run(
    repository: dict[str, Any],
    claims: list[Claim],
    plan: TestPlan,
    final_run: dict[str, Any],
    replay_run: dict[str, Any],
    risk: dict[str, Any],
    run_dir: Path,
    *,
    repaired: bool,
) -> tuple[list[ClaimResult], Scorecard, dict[str, Any]]:
    reproducibility = compare_replays(final_run, replay_run)
    atomic_json(run_dir / "reproducibility.json", reproducibility)
    if not reproducibility["reproduced"]:
        raise ReproducibilityHold(
            "clean replay did not reproduce the accepted execution contract: "
            + "; ".join(reproducibility["mismatches"])
        )
    claim_results = evaluate_claims(claims, plan, final_run)
    score = score_run(claims, claim_results, plan, final_run, risk, reproducibility, repaired=repaired)
    result = {
        "repository": repository["repository"],
        "commit_sha": repository["commit_sha"],
        "claim_results": claim_results,
        "score": score,
        "reproducibility": reproducibility,
    }
    atomic_json(run_dir / "claims-evaluated.json", {"claims": claim_results})
    atomic_json(run_dir / "score.json", score)
    atomic_json(run_dir / "evaluation.json", result)
    return claim_results, score, reproducibility


def evaluate_claims(claims: list[Claim], plan: TestPlan, execution: dict[str, Any]) -> list[ClaimResult]:
    tests = {str(test.get("test_id")): test for test in execution.get("tests", []) if isinstance(test, dict)}
    results: list[ClaimResult] = []
    for claim in claims:
        mapped = [
            test
            for spec in plan.tests
            if claim.claim_id in spec.claim_ids
            if (test := tests.get(spec.test_id))
        ]
        limitation_text = next((text for text in plan.limitations if claim.claim_id in text), None)
        limitation = limitation_text is not None
        evidence = [path for test in mapped for path in test.get("evidence", []) if isinstance(path, str)]
        if mapped and _is_install_claim(claim) and execution.get("status") == "COMPLETE":
            method = str(execution.get("installation_method") or "offline staged-source installation")
            adjustment = (
                " An automated VCS-version fallback was required."
                if execution.get("install_adjustments")
                else ""
            )
            results.append(
                ClaimResult(
                    claim.claim_id,
                    ResultState.PARTIAL,
                    [str(test["test_id"]) for test in mapped],
                    ["final.json#install", *evidence],
                    f"The exact commit completed {method}, but the documented registry command was not run verbatim."
                    + adjustment,
                    0.5,
                    0.0,
                )
            )
            continue
        if not mapped:
            results.append(
                ClaimResult(
                    claim.claim_id,
                    ResultState.UNVERIFIED,
                    [],
                    [],
                    "No accepted test exercised this claim.",
                    0.0,
                    0.0,
                )
            )
            continue
        statuses = [ResultState(test["status"]) for test in mapped]
        tested_ids = [str(test["test_id"]) for test in mapped]
        if all(status is ResultState.PASS for status in statuses):
            status = ResultState.PARTIAL if limitation else ResultState.PASS
            explanation = (
                f"Tests {', '.join(tested_ids)} passed in both clean replays. Remaining gap: {limitation_text}"
                if limitation
                else "All mapped tests passed in both clean replays."
            )
            results.append(
                ClaimResult(
                    claim.claim_id, status, tested_ids, evidence, explanation, 0.5 if limitation else 1.0, 0.0
                )
            )
        elif any(status is ResultState.FAIL for status in statuses):
            partial = any(status is ResultState.PASS for status in statuses)
            results.append(
                ClaimResult(
                    claim.claim_id,
                    ResultState.PARTIAL if partial else ResultState.FAIL,
                    tested_ids,
                    evidence,
                    "At least one mapped clean-replay assertion failed."
                    if partial
                    else "The mapped clean-replay tests failed.",
                    1.0,
                    0.5 if partial else 1.0,
                )
            )
        else:
            results.append(
                ClaimResult(
                    claim.claim_id,
                    ResultState.UNVERIFIED,
                    tested_ids,
                    evidence,
                    "Mapped tests were blocked or otherwise produced insufficient evidence.",
                    0.0,
                    0.0,
                )
            )
    return results


def compare_replays(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    mismatches: list[str] = []
    if first.get("status") != "COMPLETE" or second.get("status") != "COMPLETE":
        mismatches.append("one or both clean runs did not complete")
    provenance_fields = (
        "execution_contract_sha256",
        "dependency_bundle_sha256",
        "candidate_network",
        "track",
        "toolchain",
        "entrypoint",
        "entrypoint_installed",
        "installation_method",
        "install_controls",
        "install_adjustments",
        "manual_interventions",
    )
    for field in provenance_fields:
        if first.get(field) != second.get(field):
            mismatches.append(f"clean run {field} differed")
    for field in ("execution_contract_sha256", "dependency_bundle_sha256"):
        if any(re.fullmatch(r"[0-9a-f]{64}", str(run.get(field) or "")) is None for run in (first, second)):
            mismatches.append(f"clean run {field} was missing or invalid")
    for field in ("track", "toolchain", "entrypoint", "installation_method"):
        if any(not isinstance(run.get(field), str) or not run[field].strip() for run in (first, second)):
            mismatches.append(f"clean run {field} was missing or invalid")
    if any(
        not isinstance(run.get("install_controls"), list)
        or not run["install_controls"]
        or any(not isinstance(value, str) or not value.strip() for value in run["install_controls"])
        for run in (first, second)
    ):
        mismatches.append("clean run install_controls were missing or invalid")
    if any(not isinstance(run.get("install_adjustments"), list) for run in (first, second)):
        mismatches.append("clean run install_adjustments were missing or invalid")
    if first.get("candidate_network") != "none" or second.get("candidate_network") != "none":
        mismatches.append("candidate network was not disabled in both clean runs")
    if first.get("entrypoint_installed") is not True or second.get("entrypoint_installed") is not True:
        mismatches.append("entrypoint was not installed in both clean runs")
    if any(
        isinstance(run.get("manual_interventions"), bool)
        or not isinstance(run.get("manual_interventions"), int)
        or run["manual_interventions"] < 0
        for run in (first, second)
    ):
        mismatches.append("clean run manual_interventions were missing or invalid")
    left = {str(test.get("test_id")): test for test in first.get("tests", []) if isinstance(test, dict)}
    right = {str(test.get("test_id")): test for test in second.get("tests", []) if isinstance(test, dict)}
    if set(left) != set(right):
        mismatches.append("clean runs executed different test sets")
    for test_id in sorted(set(left) & set(right)):
        for field in ("status", "exit_code"):
            if left[test_id].get(field) != right[test_id].get(field):
                mismatches.append(f"{test_id} {field} differed")
        left_assertions = [
            (item.get("assertion"), item.get("passed")) for item in left[test_id].get("assertions", [])
        ]
        right_assertions = [
            (item.get("assertion"), item.get("passed")) for item in right[test_id].get("assertions", [])
        ]
        if left_assertions != right_assertions:
            mismatches.append(f"{test_id} assertions differed")
    return {
        "reproduced": not mismatches,
        "first_label": first.get("label"),
        "second_label": second.get("label"),
        "tests_compared": len(set(left) & set(right)),
        "mismatches": mismatches,
    }


def score_run(
    claims: list[Claim],
    claim_results: list[ClaimResult],
    plan: TestPlan,
    execution: dict[str, Any],
    risk: dict[str, Any],
    reproducibility: dict[str, Any],
    *,
    repaired: bool,
) -> Scorecard:
    by_id = {result.claim_id: result for result in claim_results}
    points = {ResultState.PASS: 100, ResultState.PARTIAL: 70, ResultState.FAIL: 0}
    scored = [
        (IMPORTANCE_WEIGHT[claim.importance], by_id[claim.claim_id])
        for claim in claims
        if by_id[claim.claim_id].status in points
    ]
    functionality = (
        round(
            sum(weight * points[result.status] for weight, result in scored)
            / sum(weight for weight, _ in scored)
        )
        if scored
        else 0
    )

    tests = execution.get("tests", [])
    core = [
        test
        for test in tests
        if not next(spec.edge_case for spec in plan.tests if spec.test_id == test.get("test_id"))
    ]
    core_ratio = (
        sum(test.get("status") == ResultState.PASS.value for test in core) / len(core) if core else 0.0
    )
    utility = 85 if core_ratio == 1 else 70 if core_ratio >= 0.75 else 40 if core_ratio else 0

    install = execution.get("install") or {}
    setup = 90 if execution.get("status") == "COMPLETE" else 0
    if int(install.get("duration_ms") or 0) > 60_000:
        setup -= 10
    setup -= min(40, int(execution.get("manual_interventions") or 0) * 10)
    adjustments = execution.get("install_adjustments") or []
    if adjustments:
        setup -= 15

    all_passed = bool(tests) and all(test.get("status") == ResultState.PASS.value for test in tests)
    reliability = (
        95
        if reproducibility.get("reproduced") and all_passed
        else 75
        if reproducibility.get("reproduced")
        else 40
    )
    ram_measurements = [
        int(value) for test in tests if isinstance((value := test.get("peak_ram_bytes")), int)
    ]
    peak_ram = max(ram_measurements, default=None)
    slowest = max((int(test.get("duration_ms") or 0) for test in tests), default=0)
    performance = (
        85
        if peak_ram is not None and peak_ram <= 256 * 1024 * 1024 and slowest <= 10_000
        else 70
        if (peak_ram is None or peak_ram <= 1024 * 1024 * 1024) and slowest <= 30_000
        else 50
    )
    documentation = 80 if repaired else 90
    safety = (
        90
        if risk.get("classification") == "TRUSTED_ENOUGH_TO_TEST"
        and execution.get("candidate_network") == "none"
        else 60
    )
    dimensions = {
        "claim_verification": functionality,
        "utility": utility,
        "setup_experience": max(0, setup),
        "reliability": reliability,
        "performance_efficiency": performance,
        "documentation": documentation,
        "safety_privacy": safety,
        "novelty": 50,
    }
    overall = round(sum(dimensions[name] * weight for name, weight in WEIGHTS.items()) / 100)
    risk_findings = [item for item in risk.get("findings", []) if isinstance(item, dict)]
    severity_counts = {
        severity: sum(item.get("severity") == severity for item in risk_findings)
        for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
    }
    static_summary = (
        "Static screening recorded "
        + ", ".join(f"{count} {severity}" for severity, count in severity_counts.items() if count)
        + " finding(s)"
        if risk_findings
        else "Static screening recorded no findings"
    )

    denominator = sum(
        IMPORTANCE_WEIGHT[claim.importance] * by_id[claim.claim_id].tested_fraction for claim in claims
    )
    numerator = sum(
        IMPORTANCE_WEIGHT[claim.importance] * by_id[claim.claim_id].unsupported_fraction for claim in claims
    )
    bullshit_ratio = round(100 * numerator / denominator) if denominator else None
    important = [claim for claim in claims if claim.importance is Importance.HIGH]
    coverage = (
        sum(
            IMPORTANCE_WEIGHT[claim.importance] * by_id[claim.claim_id].tested_fraction for claim in important
        )
        / sum(IMPORTANCE_WEIGHT[claim.importance] for claim in important)
        if important
        else 0.0
    )
    if reproducibility.get("reproduced") and coverage >= 0.8 and all_passed:
        confidence = Confidence.HIGH
    elif reproducibility.get("reproduced") and coverage >= 0.5 and tests:
        confidence = Confidence.MEDIUM
    elif tests:
        confidence = Confidence.LOW
    else:
        confidence = Confidence.INSUFFICIENT
    if execution.get("status") != "COMPLETE":
        verdict = "BROKEN"
    elif confidence is Confidence.INSUFFICIENT:
        verdict = "INSUFFICIENT EVIDENCE"
    elif overall >= 90 and confidence is Confidence.HIGH:
        verdict = "MUST TRY"
    elif overall >= 75:
        verdict = "WORTH IT"
    elif overall >= 60:
        verdict = "PROMISING"
    elif overall >= 45:
        verdict = "NICHE"
    else:
        verdict = "NOT WORTH IT"
    friction = (
        "BROKEN"
        if execution.get("status") != "COMPLETE"
        else "EASY"
        if setup >= 80
        else "MODERATE"
        if setup >= 60
        else "PAINFUL"
    )
    reasons = {
        "claim_verification": "Importance-weighted claim results; unverified claim portions reduce coverage rather than becoming failures.",
        "utility": f"{sum(test.get('status') == ResultState.PASS.value for test in core)}/{len(core)} core workflow tests ({', '.join(str(test.get('test_id')) for test in core)}) passed.",
        "setup_experience": f"{execution.get('installation_method') or 'Offline source installation'} took {int(install.get('duration_ms') or 0)} ms with {int(execution.get('manual_interventions') or 0)} manual interventions and {len(adjustments)} automated setup {'adjustment' if len(adjustments) == 1 else 'adjustments'}.",
        "reliability": f"Two clean runs compared across {reproducibility.get('tests_compared', 0)} tests; reproduced={bool(reproducibility.get('reproduced'))}.",
        "performance_efficiency": f"Slowest test was {slowest} ms and "
        + (
            f"measured peak RAM was {peak_ram} bytes"
            if peak_ram is not None
            else "peak RAM was below the 200 ms sampling resolution"
        )
        + "; no comparative baseline was used.",
        "documentation": "README-derived workflows worked; diagnostic execution was needed to correct undocumented exit/format details."
        if repaired
        else "README-derived workflows worked without contract repair.",
        "safety_privacy": f"{static_summary}; candidate execution had no network. Static review is not a safety guarantee.",
        "novelty": "Neutral score: WorthIt did not run a comparative novelty study.",
    }
    return Scorecard(
        WEIGHTS,
        dimensions,
        reasons,
        overall,
        confidence,
        verdict,
        bullshit_ratio,
        numerator,
        denominator,
        friction,
    )


def _is_install_claim(claim: Claim) -> bool:
    text = claim.text.casefold()
    return (
        "[" not in text
        and "install" in text
        and ("pip" in text or "npm" in text or "cargo" in text or "go install" in text)
    )
