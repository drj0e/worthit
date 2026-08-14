from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from .inspect import clean_text, source_tree_report
from .models import (
    AssertionResult,
    CommandTrace,
    ResultState,
    TestPlan,
    TestResult,
    TrustClass,
    atomic_json,
    jsonable,
)

IMAGE = "worthit-python:0.2"
MANAGED_LABEL = "worthit.managed=true"
REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9*+_.!-]+)?(?:\s*;\s*[A-Za-z0-9_ .<>=!'\"()andor-]+)?$"
)
WHEEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,299}\.whl$")
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:OPENSSH|RSA|EC|DSA) PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----", re.S),
]
PRIVATE_IP = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
)


class SandboxUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RunnerConfig:
    memory: str = "2g"
    cpus: str = "2"
    pids: int = 256
    workspace: str = "1g"
    output_bytes: int = 262_144
    install_timeout: int = 600
    test_timeout: int = 120
    runtime: str = "runc"
    allow_runc: bool = False

    @classmethod
    def from_env(cls, *, allow_runc: bool = False) -> RunnerConfig:
        return cls(
            memory=os.environ.get("WORTHIT_MAX_RAM", "2g"),
            cpus=os.environ.get("WORTHIT_MAX_CPUS", "2"),
            pids=int(os.environ.get("WORTHIT_MAX_PIDS", "256")),
            workspace=os.environ.get("WORTHIT_MAX_WORKSPACE", "1g"),
            output_bytes=int(os.environ.get("WORTHIT_MAX_OUTPUT_BYTES", "262144")),
            install_timeout=int(os.environ.get("WORTHIT_MAX_INSTALL_SECONDS", "600")),
            test_timeout=int(os.environ.get("WORTHIT_MAX_TEST_SECONDS", "120")),
            runtime=os.environ.get("WORTHIT_CONTAINER_RUNTIME", "runc"),
            allow_runc=allow_runc or os.environ.get("WORTHIT_ALLOW_RUNC") == "1",
        )


def verify_source_snapshot(source: Path, archive: Path, repository: dict[str, object]) -> dict[str, object]:
    expected_archive = repository.get("archive_sha256")
    expected_tree = repository.get("source_tree_sha256")
    if not isinstance(expected_archive, str) or not isinstance(expected_tree, str):
        raise ValueError("source snapshot provenance is incomplete")
    if not archive.is_file() or archive.is_symlink() or _sha256(archive) != expected_archive:
        raise ValueError("cached GitHub source archive changed")
    tree = source_tree_report(source)
    if tree["sha256"] != expected_tree:
        raise ValueError("cached source snapshot changed")
    if tree["files"] != repository.get("source_files") or tree["bytes"] != repository.get("source_bytes"):
        raise ValueError("cached source inventory changed")
    return {
        "transport": "GitHub REST commit tarball",
        "commit_sha": repository.get("commit_sha"),
        "archive_sha256": expected_archive,
        "source_tree_sha256": expected_tree,
        "files": tree["files"],
        "bytes": tree["bytes"],
        "git_parser_on_host": False,
        "lfs_smudge": False,
        "submodules": False,
        "tags_fetched": False,
    }


class DockerRunner:
    def __init__(self, run_dir: Path, config: RunnerConfig, trust: TrustClass) -> None:
        self.run_dir = run_dir.resolve()
        self.config = config
        self.trust = trust

    def preflight(self) -> dict[str, object]:
        if shutil.which("docker") is None:
            raise SandboxUnavailable("Docker is not installed")
        version = subprocess.run(
            ["docker", "version", "--format", "{{json .Server}}"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if version.returncode:
            raise SandboxUnavailable("Docker daemon is unavailable")
        info = subprocess.run(
            ["docker", "info", "--format", "{{json .}}"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if info.returncode:
            raise SandboxUnavailable("Docker security capabilities could not be inspected")
        parsed = json.loads(info.stdout)
        options = [str(value) for value in parsed.get("SecurityOptions", [])]
        runtimes = set((parsed.get("Runtimes") or {}).keys())
        runtime = self.config.runtime
        if "runsc" in runtimes and runtime == "runc":
            runtime = "runsc"
        if runtime not in runtimes:
            raise SandboxUnavailable(f"requested container runtime {runtime!r} is not installed")
        if runtime == "runc" and not self.config.allow_runc:
            raise SandboxUnavailable(
                "rootful runc is not a strong sandbox; pass --allow-runc only for a reviewed benign repository"
            )
        if runtime == "runc" and self.trust is not TrustClass.TRUSTED:
            raise SandboxUnavailable(f"runc backend refuses trust classification {self.trust.value}")
        if runtime == "runc" and (
            not any("seccomp" in option for option in options)
            or not any("apparmor" in option for option in options)
        ):
            raise SandboxUnavailable("runc backend requires both seccomp and AppArmor")
        self._runtime = runtime
        self._reap_stale()
        self._ensure_image()
        image = json.loads(
            subprocess.run(
                ["docker", "image", "inspect", IMAGE, "--format", "{{json .}}"],
                text=True,
                capture_output=True,
                timeout=15,
                check=True,
            ).stdout
        )
        self._image_id = str(image.get("Id") or "")
        if not self._image_id:
            raise SandboxUnavailable("trusted evaluator image has no immutable ID")
        report = {
            "backend": "docker",
            "runtime": runtime,
            "security_options": options,
            "rootless": any(option == "name=rootless" for option in options),
            "cgroup_version": parsed.get("CgroupVersion"),
            "image": IMAGE,
            "image_id": self._image_id,
            "image_digests": image.get("RepoDigests", []),
            "limits": {
                "memory": self.config.memory,
                "cpus": self.config.cpus,
                "pids": self.config.pids,
                "workspace": self.config.workspace,
                "output_bytes": self.config.output_bytes,
                "install_timeout": self.config.install_timeout,
                "test_timeout": self.config.test_timeout,
            },
            "candidate_network": "none",
            "host_mounts": [],
            "docker_socket_exposed": False,
            "inherited_environment": [],
            "residual_risk": "gVisor is preferred; runc shares the host kernel and is restricted to TRUSTED_ENOUGH_TO_TEST.",
        }
        atomic_json(self.run_dir / "environment.json", report)
        return report

    def execution_contract_sha256(self, plan: TestPlan, install_environment: dict[str, str]) -> str:
        context = {
            "image_id": getattr(self, "_image_id", ""),
            "runtime": getattr(self, "_runtime", ""),
            "limits": jsonable(self.config),
        }
        if not context["image_id"] or not context["runtime"]:
            raise SandboxUnavailable("runner preflight must precede execution digest calculation")
        return execution_contract_sha256(plan, install_environment, context)

    def prefetch_wheels(self, environment: dict[str, object]) -> dict[str, object]:
        build_requires = environment.get("build_requires")
        dependencies = environment.get("dependencies")
        if not isinstance(build_requires, list) or not isinstance(dependencies, list):
            raise SandboxUnavailable("detected dependency metadata is not a list")
        raw = [*build_requires, *dependencies]
        requirements = _validate_requirements([str(value) for value in raw])
        wheel_dir = self.run_dir / "wheelhouse"
        if wheel_dir.exists() and not any(wheel_dir.iterdir()):
            wheel_dir.rmdir()
        wheel_dir.mkdir(parents=True, exist_ok=False)
        name = f"worthit-fetch-{uuid.uuid4().hex[:12]}"
        create = [
            "docker",
            "create",
            "--name",
            name,
            "--label",
            MANAGED_LABEL,
            "--network",
            "bridge",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777",
            "--tmpfs",
            "/deps:rw,nosuid,nodev,noexec,size=1g,mode=1777",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "128",
            "--memory",
            "1g",
            "--memory-swap",
            "1g",
            "--cpus",
            "1",
            "--env",
            "HOME=/tmp",
            IMAGE,
            "sleep",
            "infinity",
        ]
        command = [
            "docker",
            "exec",
            "-i",
            "--user",
            "65534:65534",
            "--workdir",
            "/deps",
            "--env",
            "HOME=/tmp",
            name,
            "python",
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--dest",
            "/deps",
            *requirements,
        ]
        evidence = self.run_dir / "evidence" / "dependency-fetch"
        try:
            created = subprocess.run(create, text=True, capture_output=True, timeout=30, check=False)
            if created.returncode:
                raise SandboxUnavailable(f"could not create dependency fetcher: {created.stderr[-500:]}")
            started = subprocess.run(
                ["docker", "start", name], text=True, capture_output=True, timeout=30, check=False
            )
            if started.returncode:
                raise SandboxUnavailable(f"could not start dependency fetcher: {started.stderr[-500:]}")
            trace = run_capped(
                command,
                evidence,
                "dependency-fetch",
                self.config.install_timeout,
                self.config.output_bytes,
                None,
                name,
            )
            if trace.exit_code != 0:
                raise SandboxUnavailable(
                    "wheel-only dependency resolution failed; source distributions and direct URLs are not allowed"
                )
            _extract_wheels(name, wheel_dir)
        finally:
            subprocess.run(
                ["docker", "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        wheels = []
        total = 0
        for path in sorted(wheel_dir.iterdir()):
            if not path.is_file() or path.is_symlink() or path.suffix != ".whl":
                raise SandboxUnavailable(f"dependency fetch produced a non-wheel artifact: {path.name}")
            total += path.stat().st_size
            if total > 1024 * 1024 * 1024:
                raise SandboxUnavailable("wheelhouse exceeded 1 GB")
            wheels.append({"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)})
        report = {
            "requirements": requirements,
            "network_phase": "PyPI wheel fetch only; candidate source was not present",
            "candidate_code_executed": False,
            "wheels": wheels,
            "total_bytes": total,
            "trace": jsonable(trace),
        }
        atomic_json(self.run_dir / "dependency-fetch.json", report)
        return report

    def execute_plan(
        self,
        source: Path,
        plan: TestPlan,
        wheel_dir: Path,
        label: str,
        install_environment: dict[str, str],
        install_adjustments: list[dict[str, str]],
    ) -> dict[str, object]:
        sandbox = Sandbox(self.run_dir, self.config, getattr(self, "_runtime", self.config.runtime), label)
        started = time.monotonic()
        try:
            sandbox.start()
            sandbox.stage_directory(source, "repo")
            sandbox.stage_directory(wheel_dir, "deps")
            venv = sandbox.exec(
                ["/usr/local/bin/python", "-m", "venv", "/work/venv"],
                "/work",
                b"",
                min(120, self.config.install_timeout),
                self.run_dir / "evidence" / label / "venv",
                "provision",
            )
            install = sandbox.exec(
                [
                    "/work/venv/bin/python",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-index",
                    "--find-links",
                    "/work/deps",
                    "/work/repo",
                ],
                "/work",
                b"",
                self.config.install_timeout,
                self.run_dir / "evidence" / label / "install",
                "install",
                extra_env={
                    "PIP_NO_INDEX": "1",
                    "PIP_FIND_LINKS": "/work/deps",
                    **install_environment,
                },
            )
            installed = sandbox.path_exists(f"/work/venv/bin/{plan.entrypoint}")
            if installed:
                version_probe = sandbox.exec(
                    [f"/work/venv/bin/{plan.entrypoint}", "--version"],
                    "/work",
                    b"",
                    30,
                    self.run_dir / "evidence" / label / "version",
                    "verify-install",
                )
            else:
                version_probe = _blocked_trace(
                    "verify-install",
                    [f"/work/venv/bin/{plan.entrypoint}", "--version"],
                    "/work",
                    self.run_dir / "evidence" / label / "version",
                    "entrypoint was not installed",
                )
            tests: list[TestResult] = []
            if venv.exit_code == 0 and install.exit_code == 0 and installed:
                for spec in plan.tests:
                    sandbox.stage_case(spec.test_id, spec.setup_files)
                    trace = sandbox.exec(
                        _expand_argv(spec.argv, plan.entrypoint),
                        f"/work/cases/{spec.test_id}",
                        spec.stdin.encode("utf-8"),
                        min(spec.timeout_sec, self.config.test_timeout),
                        self.run_dir / "evidence" / label / spec.test_id,
                        "execute",
                    )
                    tests.append(evaluate_test(spec, trace, sandbox))
                    if not sandbox.alive:
                        break
            disk_bytes = sandbox.work_bytes() if sandbox.alive else None
            result: dict[str, object] = {
                "label": label,
                "execution_contract_sha256": self.execution_contract_sha256(plan, install_environment),
                "status": "COMPLETE" if install.exit_code == 0 and installed else "INSTALL_FAILED",
                "venv": venv,
                "install": install,
                "entrypoint": plan.entrypoint,
                "entrypoint_installed": installed,
                "version_probe": version_probe,
                "tests": tests,
                "workspace_bytes": disk_bytes,
                "manual_interventions": 0,
                "install_adjustments": install_adjustments,
                "candidate_network": "none",
                "duration_ms": round((time.monotonic() - started) * 1000),
                "completed_at": datetime.now(UTC).isoformat(),
            }
            atomic_json(self.run_dir / f"{label}.json", result)
            return result
        finally:
            sandbox.close()

    def _ensure_image(self) -> None:
        exists = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        if exists.returncode == 0:
            return
        context = Path(__file__).resolve().parents[1] / "sandbox"
        completed = subprocess.run(
            [
                "docker",
                "build",
                "--pull",
                "-f",
                str(context / "python.Dockerfile"),
                "-t",
                IMAGE,
                str(context),
            ],
            text=True,
            capture_output=True,
            timeout=900,
            check=False,
        )
        log = redact(completed.stdout + "\n" + completed.stderr)
        build_log = self.run_dir / "evidence" / "sandbox-image-build.txt"
        build_log.parent.mkdir(parents=True, exist_ok=True)
        build_log.write_text(log[-self.config.output_bytes :], encoding="utf-8")
        if completed.returncode:
            raise SandboxUnavailable("failed to build the trusted evaluator base image")

    def _reap_stale(self) -> None:
        # ponytail: V1 is serial; use run-id/age-aware reaping before parallel evaluation.
        listed = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label={MANAGED_LABEL}"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        for identifier in listed.stdout.split():
            subprocess.run(
                ["docker", "rm", "-f", identifier],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


class Sandbox:
    def __init__(self, run_dir: Path, config: RunnerConfig, runtime: str, label: str) -> None:
        self.run_dir = run_dir
        self.config = config
        self.runtime = runtime
        self.label = label
        self.name = f"worthit-{label}-{uuid.uuid4().hex[:10]}"
        self.alive = False

    def create_argv(self) -> list[str]:
        return [
            "docker",
            "create",
            "--name",
            self.name,
            "--label",
            MANAGED_LABEL,
            "--runtime",
            self.runtime,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            f"/work:rw,nosuid,nodev,exec,size={self.config.workspace},mode=1777",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(self.config.pids),
            "--memory",
            self.config.memory,
            "--memory-swap",
            self.config.memory,
            "--cpus",
            self.config.cpus,
            "--ulimit",
            "core=0",
            "--ulimit",
            "nofile=1024:1024",
            "--env",
            "HOME=/tmp",
            "--env",
            "LANG=C.UTF-8",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PIP_NO_INPUT=1",
            "--workdir",
            "/work",
            IMAGE,
            "sleep",
            "infinity",
        ]

    def start(self) -> None:
        created = subprocess.run(self.create_argv(), text=True, capture_output=True, timeout=30, check=False)
        if created.returncode:
            raise SandboxUnavailable(f"could not create sandbox: {created.stderr[-1000:]}")
        started = subprocess.run(
            ["docker", "start", self.name], text=True, capture_output=True, timeout=30, check=False
        )
        if started.returncode:
            self.close()
            raise SandboxUnavailable(f"could not start sandbox: {started.stderr[-1000:]}")
        self.alive = True

    def stage_directory(self, source: Path, destination: str) -> None:
        if not self.alive:
            raise SandboxUnavailable("sandbox is not running")
        with tempfile.NamedTemporaryFile(prefix="worthit-stage.", suffix=".tar") as archive:
            with tarfile.open(fileobj=archive, mode="w") as bundle:
                root = tarfile.TarInfo(destination)
                root.type = tarfile.DIRTYPE
                root.mode = 0o700
                bundle.addfile(root)
                count = 0
                total = 0
                for path in sorted(source.rglob("*")):
                    relative = path.relative_to(source)
                    if ".git" in relative.parts:
                        continue
                    count += 1
                    if count > 40_000:
                        raise SandboxUnavailable("staged directory has too many entries")
                    if path.is_file() and not path.is_symlink():
                        total += path.stat().st_size
                    if total > 750 * 1024 * 1024:
                        raise SandboxUnavailable("staged directory exceeds 750 MB")
                    bundle.add(path, arcname=(Path(destination) / relative).as_posix(), recursive=False)
            archive.flush()
            archive.seek(0)
            command = [
                "docker",
                "exec",
                "-i",
                "--user",
                "65534:65534",
                "--workdir",
                "/work",
                self.name,
                "tar",
                "-xf",
                "-",
                "--no-same-owner",
                "--no-same-permissions",
                "-C",
                "/work",
            ]
            process = subprocess.Popen(command, stdin=archive, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _, stderr = process.communicate(timeout=180)
            if process.returncode:
                raise SandboxUnavailable(
                    f"could not stage sandbox data: {stderr[-1000:].decode(errors='replace')}"
                )

    def stage_case(self, test_id: str, files: dict[str, str]) -> None:
        with tempfile.TemporaryDirectory(prefix=f"worthit-{test_id}.") as temp:
            root = Path(temp)
            for relative, content in files.items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            self.stage_directory(root, f"cases/{test_id}")

    def exec(
        self,
        argv: list[str],
        cwd: str,
        stdin: bytes,
        timeout: int,
        evidence_dir: Path,
        stage: str,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> CommandTrace:
        if not self.alive:
            return _blocked_trace(stage, argv, cwd, evidence_dir, "sandbox was already terminated")
        command = ["docker", "exec", "-i", "--user", "65534:65534", "--workdir", cwd]
        environment = {"HOME": "/tmp", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PIP_NO_INPUT": "1"}
        environment.update(extra_env or {})
        for name, value in sorted(environment.items()):
            command.extend(["--env", f"{name}={value}"])
        command.extend([self.name, *argv])
        trace = run_capped(command, evidence_dir, stage, timeout, self.config.output_bytes, stdin, self.name)
        if trace.timed_out or trace.output_limited:
            self.alive = False
        return CommandTrace(
            **{
                **trace.__dict__,
                "argv": argv,
                "cwd": cwd,
                "stdout_file": str(Path(trace.stdout_file).relative_to(self.run_dir)),
                "stderr_file": str(Path(trace.stderr_file).relative_to(self.run_dir)),
            }
        )

    def path_exists(self, path: str) -> bool:
        if not self.alive:
            return False
        result = subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "65534:65534",
                self.name,
                "/usr/local/bin/python",
                "-I",
                "-c",
                "import os,sys; raise SystemExit(0 if os.path.isfile(sys.argv[1]) and os.access(sys.argv[1], os.X_OK) else 1)",
                path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        return result.returncode == 0

    def read_case_file(self, test_id: str, relative: str) -> tuple[bool, str]:
        if not self.alive:
            return False, ""
        root = f"/work/cases/{test_id}"
        program = """import base64,pathlib,sys
root=pathlib.Path(sys.argv[1]).resolve()
path=(root/sys.argv[2]).resolve()
if not path.is_relative_to(root) or not path.is_file(): raise SystemExit(2)
data=path.read_bytes()
if len(data)>100000: raise SystemExit(3)
print(base64.b64encode(data).decode())
"""
        completed = subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "65534:65534",
                self.name,
                "/usr/local/bin/python",
                "-I",
                "-c",
                program,
                root,
                relative,
            ],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if completed.returncode:
            return False, ""
        return True, base64.b64decode(completed.stdout.strip()).decode("utf-8", errors="replace")

    def work_bytes(self) -> int | None:
        if not self.alive:
            return None
        program = """import os
total=0
for root,dirs,files in os.walk('/work',followlinks=False):
  for name in files:
    try: total+=os.lstat(os.path.join(root,name)).st_size
    except OSError: pass
print(total)
"""
        completed = subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "65534:65534",
                self.name,
                "/usr/local/bin/python",
                "-I",
                "-c",
                program,
            ],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        try:
            return int(completed.stdout.strip()) if completed.returncode == 0 else None
        except ValueError:
            return None

    def close(self) -> None:
        subprocess.run(
            ["docker", "rm", "-f", self.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        self.alive = False


def evaluate_test(spec: object, trace: CommandTrace, sandbox: Sandbox) -> TestResult:
    from .models import TestSpec

    if not isinstance(spec, TestSpec):
        raise TypeError("expected TestSpec")
    stdout = Path(
        trace.stdout_file if Path(trace.stdout_file).is_absolute() else sandbox.run_dir / trace.stdout_file
    ).read_text(encoding="utf-8")
    stderr = Path(
        trace.stderr_file if Path(trace.stderr_file).is_absolute() else sandbox.run_dir / trace.stderr_file
    ).read_text(encoding="utf-8")
    assertions = [
        AssertionResult(
            assertion=f"exit code is one of {spec.expected_exit_codes}",
            passed=trace.exit_code in spec.expected_exit_codes,
            observed=str(trace.exit_code),
        )
    ]
    assertions.extend(
        AssertionResult(f"stdout contains {expected!r}", expected in stdout, _short(stdout))
        for expected in spec.stdout_contains
    )
    assertions.extend(
        AssertionResult(f"stderr contains {expected!r}", expected in stderr, _short(stderr))
        for expected in spec.stderr_contains
    )
    for expected in spec.file_assertions:
        exists, content = sandbox.read_case_file(spec.test_id, expected.path)
        assertions.append(
            AssertionResult(
                f"{expected.path} exists={expected.exists}", exists is expected.exists, f"exists={exists}"
            )
        )
        if expected.exact_text is not None:
            assertions.append(
                AssertionResult(
                    f"{expected.path} exactly matches expected text",
                    exists and content == expected.exact_text,
                    _short(content),
                )
            )
        assertions.extend(
            AssertionResult(
                f"{expected.path} contains {needle!r}", exists and needle in content, _short(content)
            )
            for needle in expected.contains
        )
    if trace.timed_out:
        status = ResultState.BLOCKED
        reason = "TIMEOUT"
    elif trace.output_limited:
        status = ResultState.BLOCKED
        reason = "OUTPUT_LIMIT"
    elif all(assertion.passed for assertion in assertions):
        status = ResultState.PASS
        reason = None
    else:
        status = ResultState.FAIL
        reason = "one or more predeclared assertions failed"
    evidence = [trace.stdout_file, trace.stderr_file]
    return TestResult(
        test_id=spec.test_id,
        claim_ids=spec.claim_ids,
        status=status,
        duration_ms=trace.duration_ms,
        exit_code=trace.exit_code,
        peak_ram_bytes=trace.peak_ram_bytes,
        assertions=assertions,
        evidence=evidence,
        failure_reason=reason,
    )


def run_capped(
    command: list[str],
    evidence_dir: Path,
    stage: str,
    timeout: int,
    output_cap: int,
    stdin: bytes | None,
    container_name: str | None = None,
) -> CommandTrace:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stdout_final = evidence_dir / "stdout.txt"
    stderr_final = evidence_dir / "stderr.txt"
    stdout_descriptor, stdout_name = tempfile.mkstemp(prefix="worthit-stdout.")
    stderr_descriptor, stderr_name = tempfile.mkstemp(prefix="worthit-stderr.")
    os.close(stdout_descriptor)
    os.close(stderr_descriptor)
    limited = threading.Event()
    stop_monitor = threading.Event()
    peak: list[int | float | None] = [None, None]
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    threads = [
        threading.Thread(
            target=_drain, args=(process.stdout, Path(stdout_name), output_cap, limited), daemon=True
        ),
        threading.Thread(
            target=_drain, args=(process.stderr, Path(stderr_name), output_cap, limited), daemon=True
        ),
    ]
    for thread in threads:
        thread.start()
    feeder = threading.Thread(target=_feed, args=(process.stdin, stdin or b""), daemon=True)
    feeder.start()
    monitor = None
    if container_name:
        monitor = threading.Thread(
            target=_monitor_stats, args=(container_name, stop_monitor, peak), daemon=True
        )
        monitor.start()
    timed_out = False
    try:
        while process.poll() is None:
            elapsed = time.monotonic() - started
            if elapsed > timeout or limited.is_set():
                timed_out = elapsed > timeout
                if container_name:
                    subprocess.run(
                        ["docker", "kill", container_name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                        check=False,
                    )
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
            time.sleep(0.05)
        return_code = process.wait(timeout=10)
    finally:
        stop_monitor.set()
        for thread in threads:
            thread.join(timeout=5)
        feeder.join(timeout=2)
        if monitor:
            monitor.join(timeout=4)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    duration_ms = round((time.monotonic() - started) * 1000)
    stdout = redact(Path(stdout_name).read_bytes().decode("utf-8", errors="replace"))
    stderr = redact(Path(stderr_name).read_bytes().decode("utf-8", errors="replace"))
    Path(stdout_name).unlink(missing_ok=True)
    Path(stderr_name).unlink(missing_ok=True)
    stdout_final.write_text(stdout, encoding="utf-8")
    stderr_final.write_text(stderr, encoding="utf-8")
    status = (
        ResultState.PASS if return_code == 0 and not timed_out and not limited.is_set() else ResultState.FAIL
    )
    reason = (
        "TIMEOUT"
        if timed_out
        else "OUTPUT_LIMIT"
        if limited.is_set()
        else None
        if return_code == 0
        else f"exit code {return_code}"
    )
    return CommandTrace(
        stage=stage,
        argv=command,
        cwd="",
        exit_code=None if timed_out else return_code,
        status=status,
        duration_ms=duration_ms,
        peak_ram_bytes=int(peak[0]) if isinstance(peak[0], int | float) else None,
        max_cpu_percent=float(peak[1]) if isinstance(peak[1], int | float) else None,
        timed_out=timed_out,
        output_limited=limited.is_set(),
        stdout_file=str(stdout_final.resolve()),
        stderr_file=str(stderr_final.resolve()),
        stdout_sha256=_sha256(stdout_final),
        stderr_sha256=_sha256(stderr_final),
        failure_reason=reason,
    )


def _drain(stream: BinaryIO | None, destination: Path, cap: int, limited: threading.Event) -> None:
    if stream is None:
        return
    written = 0
    with destination.open("wb") as output:
        while chunk := stream.read(65_536):
            remaining = max(0, cap - written)
            if remaining:
                output.write(chunk[:remaining])
                written += min(len(chunk), remaining)
            if len(chunk) > remaining:
                limited.set()


def _feed(stream: BinaryIO | None, content: bytes) -> None:
    if stream is None:
        return
    try:
        stream.write(content)
        stream.close()
    except (BrokenPipeError, OSError):
        pass


def _monitor_stats(container: str, stop: threading.Event, peak: list[int | float | None]) -> None:
    while not stop.wait(0.2):
        try:
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}|{{.CPUPerc}}", container],
                text=True,
                capture_output=True,
                timeout=3,
                check=False,
            )
            if result.returncode:
                continue
            memory, cpu = result.stdout.strip().split("|", 1)
            current_memory = _parse_size(memory.split("/", 1)[0].strip())
            current_cpu = float(cpu.strip().rstrip("%"))
            peak[0] = max(int(peak[0] or 0), current_memory)
            peak[1] = max(float(peak[1] or 0.0), current_cpu)
        except (ValueError, subprocess.SubprocessError):
            continue


def _parse_size(value: str) -> int:
    match = re.fullmatch(r"([0-9.]+)([KMGT]?i?B)", value)
    if not match:
        return 0
    number = float(match.group(1))
    units = {
        "B": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "TiB": 1024**4,
    }
    return round(number * units[match.group(2)])


def redact(value: str) -> str:
    value = clean_text(value)
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("<redacted-secret>", value)
    value = PRIVATE_IP.sub("<private-ip>", value)
    home = str(Path.home())
    if home:
        value = value.replace(home, "<host-home>")
    return value


def _validate_requirements(requirements: list[str]) -> list[str]:
    result = []
    for requirement in requirements:
        requirement = requirement.strip()
        if not requirement:
            continue
        if any(token in requirement.casefold() for token in ("@", "://", "git+", "file:", "../", "\\")):
            raise SandboxUnavailable(f"direct/path dependency is outside the V1 policy: {requirement!r}")
        if len(requirement) > 300 or not REQUIREMENT.fullmatch(requirement):
            raise SandboxUnavailable(f"unsupported PEP 508 requirement: {requirement!r}")
        result.append(requirement)
    if len(result) > 250:
        raise SandboxUnavailable("too many direct requirements")
    return sorted(set(result))


def _extract_wheels(container: str, destination: Path) -> None:
    listed = subprocess.run(
        [
            "docker",
            "exec",
            "--user",
            "65534:65534",
            container,
            "/usr/local/bin/python",
            "-I",
            "-c",
            "import json,os; print(json.dumps(sorted(os.listdir('/deps'))))",
        ],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if listed.returncode:
        raise SandboxUnavailable(f"could not enumerate dependency wheels: {listed.stderr[-500:]}")
    try:
        names = json.loads(listed.stdout)
    except json.JSONDecodeError as error:
        raise SandboxUnavailable("dependency fetcher returned an invalid file list") from error
    if not isinstance(names, list) or len(names) > 250:
        raise SandboxUnavailable("dependency fetcher returned too many files")
    for name in names:
        if not isinstance(name, str) or not WHEEL_NAME.fullmatch(name):
            raise SandboxUnavailable(f"dependency fetch produced a non-wheel artifact: {name!r}")
        target = destination / name
        with target.open("wb") as output:
            copied = subprocess.run(
                ["docker", "exec", "--user", "65534:65534", container, "cat", f"/deps/{name}"],
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
        if copied.returncode:
            target.unlink(missing_ok=True)
            raise SandboxUnavailable(f"could not extract dependency wheel {name}: {copied.stderr[-500:]!r}")
        if target.stat().st_size > 250 * 1024 * 1024:
            target.unlink()
            raise SandboxUnavailable(f"dependency wheel exceeds 250 MB: {name}")


def _expand_argv(argv: list[str], entrypoint: str) -> list[str]:
    first = f"/work/venv/bin/{entrypoint}" if argv[0] == "{entrypoint}" else "/work/venv/bin/python"
    return [first, *argv[1:]]


def _blocked_trace(stage: str, argv: list[str], cwd: str, evidence: Path, reason: str) -> CommandTrace:
    evidence.mkdir(parents=True, exist_ok=True)
    stdout = evidence / "stdout.txt"
    stderr = evidence / "stderr.txt"
    stdout.write_text("", encoding="utf-8")
    stderr.write_text(reason + "\n", encoding="utf-8")
    return CommandTrace(
        stage,
        argv,
        cwd,
        None,
        ResultState.BLOCKED,
        0,
        None,
        None,
        False,
        False,
        str(stdout),
        str(stderr),
        _sha256(stdout),
        _sha256(stderr),
        reason,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def execution_contract_sha256(
    plan: TestPlan, install_environment: dict[str, str], runtime_context: dict[str, object]
) -> str:
    payload = json.dumps(
        {
            "plan": jsonable(plan),
            "install_environment": install_environment,
            "runtime_context": runtime_context,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def vcs_version_fallback(
    environment: dict[str, object], commit_sha: str
) -> tuple[dict[str, str], list[dict[str, str]]]:
    requirements = environment.get("build_requires")
    dynamic = environment.get("dynamic")
    project = environment.get("project_name")
    if not isinstance(requirements, list) or not isinstance(dynamic, list) or not isinstance(project, str):
        return {}, []
    names = {re.split(r"[<>=!~;\[]", str(value), maxsplit=1)[0].strip().casefold() for value in requirements}
    if "version" not in dynamic or not names & {"hatch-vcs", "setuptools-scm"}:
        return {}, []
    normalized = re.sub(r"[^A-Za-z0-9]", "_", project).upper().strip("_")
    if not normalized or not re.fullmatch(r"[A-Z0-9_]{1,100}", normalized):
        raise SandboxUnavailable("dynamic VCS project name cannot form a safe version fallback")
    name = f"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_{normalized}"
    value = f"0+worthit.{commit_sha[:12].casefold()}"
    adjustment = {
        "kind": "vcs_version_fallback",
        "environment_variable": f"SETUPTOOLS_SCM_PRETEND_VERSION,{name}",
        "value": value,
        "reason": "GitHub commit archives omit .git metadata required by the declared VCS version backend.",
    }
    return {"SETUPTOOLS_SCM_PRETEND_VERSION": value, name: value}, [adjustment]


def verify_wheelhouse(run_dir: Path) -> Path:
    report_path = run_dir / "dependency-fetch.json"
    wheel_dir = run_dir / "wheelhouse"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected = report.get("wheels")
    if not isinstance(expected, list) or not wheel_dir.is_dir():
        raise SandboxUnavailable("cached dependency evidence is incomplete")
    actual_names = {path.name for path in wheel_dir.iterdir() if path.is_file() and not path.is_symlink()}
    expected_names = {str(item.get("name")) for item in expected if isinstance(item, dict)}
    if actual_names != expected_names:
        raise SandboxUnavailable("cached wheelhouse file set changed")
    for item in expected:
        path = wheel_dir / str(item["name"])
        if path.stat().st_size != item.get("bytes") or _sha256(path) != item.get("sha256"):
            raise SandboxUnavailable(f"cached dependency wheel changed: {path.name}")
    return wheel_dir


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())


def _short(value: str, limit: int = 500) -> str:
    value = clean_text(value, limit)
    return value if len(value) < limit else value[:limit] + "…"
