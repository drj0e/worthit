from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from worthit.cli import main
from worthit.discovery import QUALIFICATION_REVISION, run_selected
from worthit.inspect import INSPECTION_REVISION
from worthit.models import atomic_json

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
DATE = NOW.date()


def candidate(repository: str, commit: str, *, selected: bool = True) -> dict[str, object]:
    value: dict[str, object] = {
        "repository": repository,
        "url": f"https://github.com/{repository}",
        "current_commit_sha": commit,
        "description": "A testable developer CLI.",
        "category": "CLI utility",
        "priority": {"total": 90},
        "metadata_qualified": True,
        "qualification": {
            "revision": QUALIFICATION_REVISION,
            "inspection_revision": INSPECTION_REVISION,
            "passed": True,
            "gates": {"risk": {"passed": True}},
        },
        "candidate_status": "SELECTED" if selected else "QUALIFIED",
        "retry_exhausted": False,
    }
    if selected:
        value["selection_date"] = DATE.isoformat()
        value["selected_at"] = NOW.isoformat()
    return value


def manifest(selected: list[dict[str, object]]) -> dict[str, object]:
    summaries = [
        {
            "repository": item["repository"],
            "url": item["url"],
            "commit_sha": item["current_commit_sha"],
            "description": item["description"],
            "category": item["category"],
            "priority": item["priority"],
            "status": item["candidate_status"],
        }
        for item in selected
    ]
    return {
        "schema_version": 1,
        "date": DATE.isoformat(),
        "generated_at": NOW.isoformat(),
        "provider": "fixture",
        "provider_errors": [],
        "execution_enabled": False,
        "counts": {
            "discovered": len(selected),
            "backlog": len(selected),
            "metadata_qualified": len(selected),
            "deep_inspected": len(selected),
            "qualified": len(selected),
            "selected": len(selected),
            "tested": 0,
            "completed": 0,
            "blocked_or_failed": 0,
        },
        "selected": summaries,
        "outcomes": [],
        "llm_cost_usd": 0,
        "best_tool": None,
        "biggest_disappointment": None,
        "interesting_not_tested": [],
        "skipped": [],
    }


def write_state(root: Path, candidates: list[dict[str, object]], hunt: dict[str, object]) -> None:
    atomic_json(
        root / "data" / "candidates.json",
        {"schema_version": 1, "updated_at": NOW.isoformat(), "candidates": candidates},
    )
    atomic_json(root / "hunts" / f"{DATE.isoformat()}.json", hunt)


def run_arguments(root: Path) -> dict[str, object]:
    return {
        "manifest_path": root / "hunts" / f"{DATE.isoformat()}.json",
        "backlog_path": root / "data" / "candidates.json",
        "work_root": root / "work",
        "reviews_root": root / "reviews",
        "site_dir": root / "site",
        "now": NOW,
        "run_date": DATE,
    }


class SelectedExecutionTests(unittest.TestCase):
    def test_cli_selection_mode_never_constructs_a_discovery_provider(self) -> None:
        report = {
            "date": DATE.isoformat(),
            "counts": {"selected": 0},
            "selected": [],
        }
        with (
            patch("worthit.cli.default_provider") as provider,
            patch("worthit.cli.run_selected", return_value=report) as selected,
        ):
            result = main(
                [
                    "daily",
                    "--run-date",
                    DATE.isoformat(),
                    "--selection-manifest",
                    f"hunts/{DATE.isoformat()}.json",
                    "--missing-evaluator-credential",
                ]
            )
        self.assertEqual(result, 0)
        provider.assert_not_called()
        selected.assert_called_once()

    def test_executes_only_the_manifest_url_and_full_commit(self) -> None:
        chosen = candidate("good/tool", "a" * 40)
        unselected = candidate("other/tool", "b" * 40, selected=False)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_state(root, [chosen, unselected], manifest([chosen]))
            with (
                patch("worthit.discovery.build_site"),
                patch(
                    "worthit.discovery.evaluate_repository",
                    return_value={
                        "score": 88,
                        "confidence": "HIGH",
                        "verdict": "WORTH IT",
                        "llm_cost_usd": 1.25,
                    },
                ) as evaluate,
            ):
                report = run_selected(**run_arguments(root), execute=True, allow_runc=True)

            evaluate.assert_called_once()
            call = evaluate.call_args
            self.assertEqual(call.args, ("https://github.com/good/tool",))
            self.assertEqual(call.kwargs["commit"], "a" * 40)
            state = json.loads((root / "data" / "candidates.json").read_text())
            by_repository = {item["repository"]: item for item in state["candidates"]}
            self.assertEqual(by_repository["good/tool"]["evaluation_attempts"], 1)
            self.assertNotIn("evaluation_attempts", by_repository["other/tool"])
            self.assertEqual(report["selected"][0]["commit_sha"], "a" * 40)

    def test_missing_credential_records_block_without_attempting_execution(self) -> None:
        chosen = candidate("good/tool", "a" * 40)
        chosen["evaluation_attempts"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_state(root, [chosen], manifest([chosen]))
            with (
                patch("worthit.discovery.build_site"),
                patch("worthit.discovery.evaluate_repository") as evaluate,
            ):
                report = run_selected(**run_arguments(root), missing_evaluator_credential=True)

            evaluate.assert_not_called()
            state = json.loads((root / "data" / "candidates.json").read_text())["candidates"][0]
            self.assertEqual(state["evaluation_attempts"], 1)
            self.assertEqual(state["candidate_status"], "BLOCKED_MISSING_EVALUATOR_CREDENTIAL")
            self.assertEqual(report["outcomes"][0]["status"], "BLOCKED")
            self.assertEqual(
                report["outcomes"][0]["reason_code"],
                "BLOCKED_MISSING_EVALUATOR_CREDENTIAL",
            )
            self.assertEqual(report["counts"]["blocked_or_failed"], 1)
            self.assertEqual(report["counts"]["tested"], 0)

    def test_zero_selection_is_a_noop_in_both_auth_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_state(root, [], manifest([]))
            with (
                patch("worthit.discovery.build_site"),
                patch("worthit.discovery.evaluate_repository") as evaluate,
            ):
                blocked = run_selected(**run_arguments(root), missing_evaluator_credential=True)
                executed = run_selected(**run_arguments(root), execute=True, allow_runc=True)
            evaluate.assert_not_called()
            self.assertEqual(blocked["selected"], [])
            self.assertEqual(blocked["outcomes"], [])
            self.assertEqual(executed["selected"], [])
            self.assertEqual(executed["outcomes"], [])

    def test_manifest_rejects_date_identity_qualification_and_backlog_drift(self) -> None:
        chosen = candidate("good/tool", "a" * 40)

        def wrong_date(backlog: list[dict[str, object]], hunt: dict[str, object]) -> None:
            hunt["date"] = "2026-08-13"

        def short_commit(backlog: list[dict[str, object]], hunt: dict[str, object]) -> None:
            selected = hunt["selected"]
            assert isinstance(selected, list) and isinstance(selected[0], dict)
            selected[0]["commit_sha"] = "a" * 7

        def stale_qualification(backlog: list[dict[str, object]], hunt: dict[str, object]) -> None:
            qualification = backlog[0]["qualification"]
            assert isinstance(qualification, dict)
            qualification["revision"] = QUALIFICATION_REVISION - 1

        def backlog_drift(backlog: list[dict[str, object]], hunt: dict[str, object]) -> None:
            backlog.append(candidate("extra/tool", "b" * 40))

        cases = {
            "date": wrong_date,
            "full commit": short_commit,
            "qualification": stale_qualification,
            "backlog": backlog_drift,
        }
        for name, mutate in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                backlog = [copy.deepcopy(chosen)]
                hunt = manifest([chosen])
                mutate(backlog, hunt)
                write_state(root, backlog, hunt)
                with (
                    patch("worthit.discovery.build_site"),
                    self.assertRaises(ValueError),
                ):
                    run_selected(**run_arguments(root), missing_evaluator_credential=True)

    def test_manifest_rejects_more_than_five_candidates(self) -> None:
        selected = [candidate(f"owner/tool-{index}", f"{index:x}" * 40) for index in range(6)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_state(root, selected, manifest(selected))
            with patch("worthit.discovery.build_site"), self.assertRaises(ValueError):
                run_selected(**run_arguments(root), missing_evaluator_credential=True)


if __name__ == "__main__":
    unittest.main()
