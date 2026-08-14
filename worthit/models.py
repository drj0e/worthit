from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, cast


class ResultState(StrEnum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TrustClass(StrEnum):
    TRUSTED = "TRUSTED_ENOUGH_TO_TEST"
    RESTRICTED = "TEST_WITH_RESTRICTIONS"
    REVIEW = "REQUIRES_REVIEW"
    REJECTED = "REJECTED"
    INSUFFICIENT = "INSUFFICIENT_INFORMATION"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class Importance(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Testability(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNTESTABLE = "UNTESTABLE"


CLAIM_ID = re.compile(r"^CLAIM-\d{3}$")
TEST_ID = re.compile(r"^T\d{2}$")
SAFE_ENTRYPOINT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def _text(value: Any, field_name: str, limit: int = 2_000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    text = value.strip()
    if not text or len(text) > limit or "\x00" in text:
        raise ValueError(f"invalid {field_name}")
    return text


def safe_relative(value: Any, field_name: str = "path") -> str:
    value = _text(value, field_name, 240).replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe {field_name}: {value!r}")
    return path.as_posix()


def _string_list(value: Any, field_name: str, *, limit: int = 20, item_limit: int = 1_000) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{field_name} must be a list with at most {limit} items")
    return [_text(item, field_name, item_limit) for item in value]


@dataclass(frozen=True)
class Claim:
    claim_id: str
    source: str
    source_excerpt: str
    text: str
    importance: Importance
    testability: Testability
    rationale: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Claim:
        claim_id = _text(raw.get("claim_id"), "claim_id", 16)
        if not CLAIM_ID.fullmatch(claim_id):
            raise ValueError(f"invalid claim id: {claim_id}")
        source = _text(raw.get("source"), "claim source", 240)
        return cls(
            claim_id=claim_id,
            source=source,
            source_excerpt=_text(raw.get("source_excerpt"), "claim source excerpt", 800),
            text=_text(raw.get("text"), "claim text", 800),
            importance=Importance(_text(raw.get("importance"), "claim importance", 16)),
            testability=Testability(_text(raw.get("testability"), "claim testability", 16)),
            rationale=_text(raw.get("rationale"), "claim rationale", 800),
        )


@dataclass(frozen=True)
class FileAssertion:
    path: str
    exists: bool = True
    exact_text: str | None = None
    contains: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FileAssertion:
        exact = raw.get("exact_text")
        if exact is not None and (not isinstance(exact, str) or len(exact) > 100_000):
            raise ValueError("file assertion exact_text must be capped text")
        return cls(
            path=safe_relative(raw.get("path"), "assertion path"),
            exists=bool(raw.get("exists", True)),
            exact_text=exact,
            contains=_string_list(raw.get("contains", []), "file contains", limit=20, item_limit=2_000),
        )


@dataclass(frozen=True)
class TestSpec:
    test_id: str
    claim_ids: list[str]
    purpose: str
    argv: list[str]
    stdin: str
    setup_files: dict[str, str]
    expected_exit_codes: list[int]
    stdout_contains: list[str]
    stderr_contains: list[str]
    file_assertions: list[FileAssertion]
    timeout_sec: int
    edge_case: bool
    required_resources: list[str]
    evidence: list[str]

    @classmethod
    def from_dict(cls, raw: dict[str, Any], known_claims: set[str]) -> TestSpec:
        test_id = _text(raw.get("test_id"), "test_id", 8)
        if not TEST_ID.fullmatch(test_id):
            raise ValueError(f"invalid test id: {test_id}")
        claim_ids = _string_list(raw.get("claim_ids", []), "claim_ids", limit=12, item_limit=16)
        if not claim_ids or not set(claim_ids) <= known_claims:
            raise ValueError(f"{test_id} references unknown or no claims")
        argv = _string_list(raw.get("argv"), "argv", limit=40, item_limit=2_000)
        if not argv or argv[0] not in {"{entrypoint}", "{python}"}:
            raise ValueError(f"{test_id} must invoke {{entrypoint}} or {{python}} directly")
        setup_raw = raw.get("setup_files", {})
        if not isinstance(setup_raw, dict) or len(setup_raw) > 20:
            raise ValueError("setup_files must be an object with at most 20 files")
        setup: dict[str, str] = {}
        total = 0
        for name, content in setup_raw.items():
            path = safe_relative(name, "setup file")
            if not isinstance(content, str) or "\x00" in content:
                raise ValueError("setup file content must be text")
            total += len(content.encode("utf-8"))
            if total > 250_000:
                raise ValueError("setup files exceed 250 KB")
            setup[path] = content
        exits = raw.get("expected_exit_codes", [0])
        if (
            not isinstance(exits, list)
            or not exits
            or len(exits) > 8
            or any(not isinstance(code, int) for code in exits)
        ):
            raise ValueError("expected_exit_codes must be a short integer list")
        timeout = raw.get("timeout_sec", 30)
        if not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise ValueError("timeout_sec must be between 1 and 120")
        stdin = raw.get("stdin", "")
        if not isinstance(stdin, str) or len(stdin.encode("utf-8")) > 250_000 or "\x00" in stdin:
            raise ValueError("stdin must be capped text")
        assertions = raw.get("file_assertions", [])
        if not isinstance(assertions, list) or len(assertions) > 20:
            raise ValueError("file_assertions must be a short list")
        return cls(
            test_id=test_id,
            claim_ids=claim_ids,
            purpose=_text(raw.get("purpose"), "test purpose", 800),
            argv=argv,
            stdin=stdin,
            setup_files=setup,
            expected_exit_codes=exits,
            stdout_contains=_string_list(raw.get("stdout_contains", []), "stdout_contains", item_limit=2_000),
            stderr_contains=_string_list(raw.get("stderr_contains", []), "stderr_contains", item_limit=2_000),
            file_assertions=[FileAssertion.from_dict(item) for item in assertions],
            timeout_sec=timeout,
            edge_case=bool(raw.get("edge_case", False)),
            required_resources=_string_list(raw.get("required_resources", ["CPU"]), "required_resources"),
            evidence=_string_list(raw.get("evidence", ["stdout", "stderr", "exit_code"]), "evidence"),
        )


@dataclass(frozen=True)
class TestPlan:
    core_workflow: str
    entrypoint: str
    tests: list[TestSpec]
    designer: str
    limitations: list[str]

    @classmethod
    def from_dict(cls, raw: dict[str, Any], claims: list[Claim], designer: str) -> TestPlan:
        known = {claim.claim_id for claim in claims}
        entrypoint = _text(raw.get("entrypoint"), "entrypoint", 80)
        if not SAFE_ENTRYPOINT.fullmatch(entrypoint):
            raise ValueError("entrypoint is not a safe CLI name")
        tests_raw = raw.get("tests")
        if not isinstance(tests_raw, list) or not 2 <= len(tests_raw) <= 12:
            raise ValueError("a plan needs 2 to 12 tests")
        tests = [TestSpec.from_dict(item, known) for item in tests_raw]
        ids = [test.test_id for test in tests]
        if len(ids) != len(set(ids)):
            raise ValueError("test ids must be unique")
        if not any(test.edge_case for test in tests):
            raise ValueError("a plan needs at least one edge/failure test")
        return cls(
            core_workflow=_text(raw.get("core_workflow"), "core workflow", 1_000),
            entrypoint=entrypoint,
            tests=tests,
            designer=designer,
            limitations=_string_list(raw.get("limitations", []), "plan limitations", limit=20),
        )


@dataclass(frozen=True)
class RiskFinding:
    severity: str
    category: str
    path: str
    line: int | None
    evidence: str


@dataclass(frozen=True)
class RiskReport:
    classification: TrustClass
    rationale: str
    findings: list[RiskFinding]
    files_scanned: int
    bytes_scanned: int
    limitations: list[str]


@dataclass(frozen=True)
class CommandTrace:
    stage: str
    argv: list[str]
    cwd: str
    exit_code: int | None
    status: ResultState
    duration_ms: int
    peak_ram_bytes: int | None
    max_cpu_percent: float | None
    timed_out: bool
    output_limited: bool
    stdout_file: str
    stderr_file: str
    stdout_sha256: str
    stderr_sha256: str
    failure_reason: str | None = None


@dataclass(frozen=True)
class AssertionResult:
    assertion: str
    passed: bool
    observed: str


@dataclass(frozen=True)
class TestResult:
    test_id: str
    claim_ids: list[str]
    status: ResultState
    duration_ms: int
    exit_code: int | None
    peak_ram_bytes: int | None
    assertions: list[AssertionResult]
    evidence: list[str]
    failure_reason: str | None = None


@dataclass(frozen=True)
class ClaimResult:
    claim_id: str
    status: ResultState
    test_ids: list[str]
    evidence: list[str]
    explanation: str
    tested_fraction: float
    unsupported_fraction: float


@dataclass(frozen=True)
class Scorecard:
    weights: dict[str, int]
    dimensions: dict[str, int]
    reasons: dict[str, str]
    overall: int
    confidence: Confidence
    verdict: str
    bullshit_ratio: int | None
    bullshit_numerator: float
    bullshit_denominator: float
    setup_friction: str


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: jsonable(item) for key, item in asdict(cast(Any, value)).items()}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(jsonable(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value
