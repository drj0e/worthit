from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worthit.planning import call_claude, model_cost


class ModelBudgetTests(unittest.TestCase):
    def test_cached_response_requires_a_valid_cost_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "claims-model"
            response = subprocess.CompletedProcess(
                [], 0, json.dumps({"structured_output": {}, "total_cost_usd": 0.1}), ""
            )
            with (
                patch.dict("os.environ", {"WORTHIT_MAX_LLM_COST_PER_REPO": "1"}),
                patch("worthit.planning.shutil.which", return_value="/usr/bin/claude"),
                patch("worthit.planning.subprocess.run", return_value=response),
            ):
                self.assertEqual(call_claude("test", {}, artifact, budget_usd=0.5), {})
            ledger = json.loads((artifact / "cost.json").read_text())
            ledger["status"] = "INVALID"
            (artifact / "cost.json").write_text(json.dumps(ledger), encoding="utf-8")
            with (
                patch.dict("os.environ", {"WORTHIT_MAX_LLM_COST_PER_REPO": "1"}),
                patch("worthit.planning.shutil.which", return_value="/usr/bin/claude"),
                patch("worthit.planning.subprocess.run") as invoke,
                self.assertRaisesRegex(RuntimeError, "cost limit reached"),
            ):
                call_claude("test", {}, artifact, budget_usd=0.5)
            invoke.assert_not_called()

    def test_timeout_reservation_survives_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            artifact = run_dir / "claims-model"
            with (
                patch.dict("os.environ", {"WORTHIT_MAX_LLM_COST_PER_REPO": "1.5"}),
                patch("worthit.planning.shutil.which", return_value="/usr/bin/claude"),
                patch(
                    "worthit.planning.subprocess.run",
                    side_effect=subprocess.TimeoutExpired("claude", 360),
                ),
                self.assertRaisesRegex(TimeoutError, "timed out"),
            ):
                call_claude("test", {}, artifact, budget_usd=0.75)
            self.assertEqual(model_cost(run_dir), 0.75)
            ledger = json.loads((artifact / "cost.json").read_text())
            self.assertEqual(ledger["status"], "TIMEOUT")
            with (
                patch.dict("os.environ", {"WORTHIT_MAX_LLM_COST_PER_REPO": "1.5"}),
                patch("worthit.planning.shutil.which", return_value="/usr/bin/claude"),
                patch(
                    "worthit.planning.subprocess.run",
                    side_effect=subprocess.TimeoutExpired("claude", 360),
                ),
                self.assertRaisesRegex(TimeoutError, "timed out"),
            ):
                call_claude("test", {}, artifact, budget_usd=0.75)
            self.assertEqual(model_cost(run_dir), 1.5)
            with (
                patch.dict("os.environ", {"WORTHIT_MAX_LLM_COST_PER_REPO": "1.5"}),
                patch("worthit.planning.shutil.which", return_value="/usr/bin/claude"),
                patch("worthit.planning.subprocess.run") as invoke,
                self.assertRaisesRegex(RuntimeError, "cost limit reached"),
            ):
                call_claude("test", {}, artifact, budget_usd=0.75)
            invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
