from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import tarfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, cast

from .models import SAFE_ENTRYPOINT, RiskFinding, RiskReport, TrustClass, atomic_json, safe_relative

GITHUB_REPO = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
BIDI = dict.fromkeys(map(ord, "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"))
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACT_BYTES = 250 * 1024 * 1024
MAX_FILES = 30_000
INSPECTION_REVISION = 1
OPEN_SOURCE_SPDX = {
    "0BSD",
    "AGPL-3.0",
    "AGPL-3.0-only",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC0-1.0",
    "EPL-2.0",
    "EUPL-1.2",
    "GPL-2.0",
    "GPL-2.0-only",
    "GPL-3.0",
    "GPL-3.0-only",
    "ISC",
    "LGPL-2.1",
    "LGPL-2.1-only",
    "LGPL-3.0",
    "LGPL-3.0-only",
    "MIT",
    "MPL-2.0",
    "Unlicense",
    "Zlib",
}

IMPORTANT_NAMES = {
    "README",
    "README.md",
    "README.rst",
    "LICENSE",
    "LICENSE.md",
    "COPYING",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "uv.lock",
    "poetry.lock",
    "Pipfile",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "Makefile",
    "CMakeLists.txt",
    "Dockerfile",
    "docker-compose.yml",
    "CONTRIBUTING.md",
    "INSTALL.md",
    "BUILDING.md",
}


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.slug}"


def parse_repo_url(url: str) -> RepoRef:
    match = GITHUB_REPO.fullmatch(url.strip())
    if not match:
        raise ValueError("expected a public https://github.com/owner/repository URL")
    owner, name = match.groups()
    if owner in {".", ".."} or name in {".", ".."}:
        raise ValueError("repository owner and name cannot be path traversal segments")
    return RepoRef(owner, name)


def resolve_commit(url: str, commit: str | None = None) -> str:
    ref = parse_repo_url(url)
    if commit and not COMMIT.fullmatch(commit):
        raise ValueError("commit must be a hexadecimal Git SHA")
    client = GitHubClient()
    repository = _expect_object(client.json(f"/repos/{ref.slug}"), "repository")
    if bool(repository.get("private")):
        raise ValueError("WorthIt V1 only accepts public repositories")
    resolved = _expect_object(
        client.json(f"/repos/{ref.slug}/commits/{commit or repository['default_branch']}"), "commit"
    )
    return str(resolved["sha"])


def clean_text(value: str, limit: int = 200_000) -> str:
    value = ANSI.sub("", value).translate(BIDI)
    value = "".join(char for char in value if char in "\n\r\t" or ord(char) >= 32)
    return value[:limit]


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    def _request(self, path: str, *, accept: str = "application/vnd.github+json") -> urllib.request.Request:
        headers = {
            "Accept": accept,
            "User-Agent": "worthit-lab/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(f"https://api.github.com{path}", headers=headers)

    def json(self, path: str, *, optional: bool = False) -> dict[str, Any] | list[Any] | None:
        try:
            with urllib.request.urlopen(self._request(path), timeout=30) as response:
                if int(response.headers.get("Content-Length", "0") or 0) > 5_000_000:
                    raise ValueError("GitHub JSON response is unexpectedly large")
                raw = response.read(5_000_001)
        except urllib.error.HTTPError as error:
            if optional and error.code == 404:
                return None
            raise RuntimeError(f"GitHub API {path} returned HTTP {error.code}") from error
        if len(raw) > 5_000_000:
            raise ValueError("GitHub JSON response exceeded 5 MB")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict | list):
            raise ValueError("GitHub JSON response was not an object or array")
        return parsed

    def download(self, path: str, destination: Path, limit: int = MAX_ARCHIVE_BYTES) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        total = 0
        try:
            with (
                urllib.request.urlopen(
                    self._request(path, accept="application/vnd.github+json"), timeout=60
                ) as response,
                destination.open("wb") as output,
            ):
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise ValueError(f"download exceeded {limit} bytes")
                    digest.update(chunk)
                    output.write(chunk)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return digest.hexdigest()


def inspect_repository(url: str, run_dir: Path, commit: str | None = None) -> dict[str, Any]:
    ref = parse_repo_url(url)
    if commit and not COMMIT.fullmatch(commit):
        raise ValueError("commit must be a hexadecimal Git SHA")
    client = GitHubClient()
    repo = _expect_object(client.json(f"/repos/{ref.slug}"), "repository")
    if bool(repo.get("private")):
        raise ValueError("WorthIt V1 only accepts public repositories")
    resolved = _expect_object(
        client.json(f"/repos/{ref.slug}/commits/{commit or repo['default_branch']}"), "commit"
    )
    sha = str(resolved["sha"])
    readme_response = client.json(f"/repos/{ref.slug}/readme?ref={sha}", optional=True)
    readme = ""
    readme_path = "README"
    if isinstance(readme_response, dict):
        readme_path = str(readme_response.get("path") or readme_path)
        encoded = str(readme_response.get("content") or "")
        if readme_response.get("encoding") == "base64":
            readme = clean_text(base64.b64decode(encoded, validate=False).decode("utf-8", errors="replace"))
    owner = _expect_object(client.json(f"/users/{ref.owner}"), "owner")
    release = client.json(f"/repos/{ref.slug}/releases/latest", optional=True)
    contributors = client.json(f"/repos/{ref.slug}/contributors?per_page=10&anon=1", optional=True)

    archive = run_dir / "source.tar.gz"
    archive_sha256 = client.download(f"/repos/{ref.slug}/tarball/{sha}", archive)
    source_dir = run_dir / "source"
    extracted = safe_extract_tar(archive, source_dir)
    source_tree = source_tree_report(source_dir)
    if source_tree["files"] != extracted["files"] or source_tree["bytes"] != extracted["bytes"]:
        raise ValueError("extracted source inventory changed before inspection")
    environment = detect_environment(source_dir)
    important = collect_important_evidence(source_dir)
    api_license = str((repo.get("license") or {}).get("spdx_id") or "")
    declared_license = str(environment.get("declared_license") or "")
    license_id = (
        api_license
        if api_license not in {"", "NOASSERTION", "OTHER"}
        else declared_license
        if declared_license in OPEN_SOURCE_SPDX
        else api_license
    )
    metadata = {
        "repository": ref.slug,
        "owner": ref.owner,
        "name": ref.name,
        "url": ref.url,
        "description": clean_text(str(repo.get("description") or ""), 2_000),
        "primary_language": repo.get("language"),
        "topics": [clean_text(str(topic), 120) for topic in repo.get("topics", [])[:50]],
        "license": license_id,
        "license_source": "GitHub license API" if license_id == api_license else "package.json",
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "stars": int(repo.get("stargazers_count") or 0),
        "forks": int(repo.get("forks_count") or 0),
        "archived": bool(repo.get("archived")),
        "fork": bool(repo.get("fork")),
        "default_branch": repo.get("default_branch"),
        "commit_sha": sha,
        "commit_date": ((resolved.get("commit") or {}).get("committer") or {}).get("date"),
        "commit_verified": bool(((resolved.get("commit") or {}).get("verification") or {}).get("verified")),
        "release": _release_summary(release),
        "owner_type": owner.get("type"),
        "owner_created_at": owner.get("created_at"),
        "contributors_sample": len(contributors) if isinstance(contributors, list) else None,
        "readme_path": readme_path,
        "readme": readme,
        "archive_sha256": archive_sha256,
        "archive_bytes": archive.stat().st_size,
        "source_tree_sha256": source_tree["sha256"],
        "source_files": extracted["files"],
        "source_bytes": extracted["bytes"],
        "skipped_archive_entries": extracted["skipped"],
        "environment": environment,
        "important_evidence": important,
        "inspection_revision": INSPECTION_REVISION,
        "inspected_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(run_dir / "repository.json", metadata)
    return metadata


def refresh_repository_inspection(repository: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    if repository.get("inspection_revision") == INSPECTION_REVISION:
        return repository
    source = run_dir / "source"
    tree = source_tree_report(source)
    if (
        tree["sha256"] != repository.get("source_tree_sha256")
        or tree["files"] != repository.get("source_files")
        or tree["bytes"] != repository.get("source_bytes")
    ):
        raise ValueError("cached source changed before inspection refresh")
    repository["environment"] = detect_environment(source)
    repository["important_evidence"] = collect_important_evidence(source)
    repository["inspection_revision"] = INSPECTION_REVISION
    repository["inspected_at"] = datetime.now(UTC).isoformat()
    atomic_json(run_dir / "repository.json", repository)
    return repository


def _expect_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"GitHub {label} response was not an object")
    return value


def _release_summary(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "tag": clean_text(str(value.get("tag_name") or ""), 240),
        "name": clean_text(str(value.get("name") or ""), 500),
        "published_at": value.get("published_at"),
        "prerelease": bool(value.get("prerelease")),
    }


def safe_extract_tar(archive: Path, destination: Path) -> dict[str, int]:
    destination.mkdir(parents=True, exist_ok=False)
    files = 0
    total = 0
    skipped = 0
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > MAX_FILES:
            raise ValueError(f"archive has more than {MAX_FILES} entries")
        roots = {Path(member.name).parts[0] for member in members if Path(member.name).parts}
        if len(roots) != 1:
            raise ValueError("GitHub archive must have one root directory")
        root = roots.pop()
        for member in members:
            parts = Path(member.name).parts
            if not parts or parts[0] != root:
                raise ValueError("archive root changed during extraction")
            relative_parts = parts[1:]
            if not relative_parts:
                continue
            if any(part in {"", ".", ".."} for part in relative_parts):
                raise ValueError(f"unsafe archive path: {member.name}")
            target = destination.joinpath(*relative_parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                skipped += 1
                continue
            total += member.size
            files += 1
            if total > MAX_EXTRACT_BYTES:
                raise ValueError(f"archive expands beyond {MAX_EXTRACT_BYTES} bytes")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError(f"could not read archive member {member.name}")
            _copy_capped(source, target, member.size)
            target.chmod(0o755 if member.mode & stat.S_IXUSR else 0o644)
    return {"files": files, "bytes": total, "skipped": skipped}


def source_tree_report(source: Path) -> dict[str, int | str]:
    """Hash the inert extracted tree without invoking candidate tooling."""
    if not source.is_dir() or source.is_symlink():
        raise ValueError("source snapshot is not a real directory")
    digest = hashlib.sha256()
    files = 0
    directories = 0
    total = 0
    for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()):
        if path.is_symlink():
            raise ValueError(f"source snapshot contains a symlink: {path.relative_to(source)}")
        relative = path.relative_to(source).as_posix().encode("utf-8")
        if path.is_dir():
            directories += 1
            digest.update(b"D\0" + relative + b"\0")
            continue
        if not path.is_file():
            raise ValueError(f"source snapshot contains a special file: {path.relative_to(source)}")
        files += 1
        size = path.stat().st_size
        total += size
        if files > MAX_FILES or total > MAX_EXTRACT_BYTES:
            raise ValueError("source snapshot exceeds V1 limits")
        digest.update(b"F\0" + relative + b"\0" + str(size).encode() + b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return {"sha256": digest.hexdigest(), "files": files, "directories": directories, "bytes": total}


def _copy_capped(source: IO[bytes], target: Path, expected: int) -> None:
    written = 0
    with target.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            written += len(chunk)
            if written > expected:
                raise ValueError("archive member exceeded declared size")
            output.write(chunk)
    if written != expected:
        raise ValueError("archive member size did not match header")


def detect_environment(source_dir: Path) -> dict[str, Any]:
    markers = sorted(
        path.relative_to(source_dir).as_posix()
        for path in source_dir.rglob("*")
        if path.is_file() and path.name in IMPORTANT_NAMES
    )
    result: dict[str, Any] = {"markers": markers[:200], "track": None, "supported": False}
    pyproject = source_dir / "pyproject.toml"
    if not pyproject.exists():
        package = source_dir / "package.json"
        if package.exists():
            return _detect_node_cli(source_dir, package, result)
        go_mod = source_dir / "go.mod"
        if go_mod.exists():
            return _detect_go_cli(source_dir, go_mod, result)
        return result
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8")[:2_000_000])
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        result["parse_error"] = str(error)
        return result
    project_value = data.get("project")
    build_value = data.get("build-system")
    project = cast(dict[str, Any], project_value) if isinstance(project_value, dict) else {}
    build = cast(dict[str, Any], build_value) if isinstance(build_value, dict) else {}
    scripts_value = project.get("scripts")
    dependencies_value = project.get("dependencies")
    build_requires_value = build.get("requires")
    dynamic_value = project.get("dynamic")
    scripts = cast(dict[str, Any], scripts_value) if isinstance(scripts_value, dict) else {}
    dependencies = dependencies_value if isinstance(dependencies_value, list) else []
    build_requires = build_requires_value if isinstance(build_requires_value, list) else []
    dynamic = dynamic_value if isinstance(dynamic_value, list) else []
    result.update(
        {
            "track": "python-cli",
            "supported": bool(scripts) and "dependencies" not in dynamic,
            "project_name": project.get("name"),
            "project_version": project.get("version"),
            "requires_python": project.get("requires-python"),
            "entrypoints": {str(key): str(value) for key, value in list(scripts.items())[:30]},
            "dependencies": [str(value) for value in dependencies[:200]],
            "build_requires": [str(value) for value in build_requires[:50]],
            "build_backend": build.get("build-backend"),
            "dynamic": [str(value) for value in dynamic],
            "lockfiles": [
                name for name in ("uv.lock", "poetry.lock", "Pipfile.lock") if (source_dir / name).exists()
            ],
        }
    )
    return result


def _detect_node_cli(source_dir: Path, package: Path, result: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(package.read_text(encoding="utf-8")[:2_000_000])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        result["parse_error"] = str(error)
        return result
    if not isinstance(raw, dict):
        result["parse_error"] = "package.json is not an object"
        return result
    name = raw.get("name")
    bin_value = raw.get("bin")
    bins: dict[str, str] = {}
    if isinstance(bin_value, str) and isinstance(name, str):
        bins[name.rsplit("/", 1)[-1]] = bin_value
    elif isinstance(bin_value, dict):
        bins = {str(key): str(value) for key, value in list(bin_value.items())[:30]}
    entrypoints: dict[str, str] = {}
    invalid_bins: list[str] = []
    for entrypoint, target_value in bins.items():
        try:
            target = safe_relative(target_value, "Node bin target")
        except ValueError:
            invalid_bins.append(entrypoint)
            continue
        path = source_dir / target
        if SAFE_ENTRYPOINT.fullmatch(entrypoint) and path.is_file() and not path.is_symlink():
            entrypoints[entrypoint] = target
        else:
            invalid_bins.append(entrypoint)
    dependency_fields = ("dependencies", "optionalDependencies", "peerDependencies")
    invalid_dependency_fields = [
        field
        for field in dependency_fields
        if raw.get(field) is not None and not isinstance(raw.get(field), dict)
    ]
    dependencies = {
        field: value for field in dependency_fields if isinstance((value := raw.get(field)), dict) and value
    }
    scripts_value = raw.get("scripts")
    scripts = cast(dict[str, Any], scripts_value) if isinstance(scripts_value, dict) else {}
    lifecycle = {
        key: str(scripts[key])
        for key in ("preinstall", "install", "postinstall", "prepare")
        if key in scripts
    }
    result.update(
        {
            "track": "node-cli",
            "supported": bool(entrypoints)
            and not invalid_bins
            and not dependencies
            and not invalid_dependency_fields
            and not lifecycle,
            "project_name": name,
            "project_version": raw.get("version"),
            "declared_license": raw.get("license") if isinstance(raw.get("license"), str) else None,
            "requires_node": (raw.get("engines") or {}).get("node")
            if isinstance(raw.get("engines"), dict)
            else None,
            "entrypoints": entrypoints,
            "dependencies": dependencies,
            "invalid_dependency_fields": invalid_dependency_fields,
            "lifecycle_scripts": lifecycle,
            "install_strategy": "npm-source-offline-ignore-scripts",
            "invalid_bins": invalid_bins,
            "lockfiles": [
                name
                for name in ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock")
                if (source_dir / name).exists()
            ],
        }
    )
    return result


def _detect_go_cli(source_dir: Path, go_mod: Path, result: dict[str, Any]) -> dict[str, Any]:
    try:
        text = go_mod.read_text(encoding="utf-8")[:2_000_000]
    except (OSError, UnicodeDecodeError) as error:
        result["parse_error"] = str(error)
        return result
    module_match = re.search(r"(?m)^\s*module\s+(\S+)\s*$", text)
    version_match = re.search(r"(?m)^\s*go\s+([0-9]+(?:\.[0-9]+){1,2})\s*$", text)
    module = module_match.group(1) if module_match else ""
    entrypoint = module.rstrip("/").rsplit("/", 1)[-1]
    requirements: list[str] = []
    in_require = False
    for line in text.splitlines():
        stripped = line.split("//", 1)[0].strip()
        if stripped == "require (":
            in_require = True
            continue
        if in_require and stripped == ")":
            in_require = False
            continue
        if stripped.startswith("require "):
            stripped = stripped.removeprefix("require ").strip()
        elif not in_require:
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            requirements.append(f"{parts[0]} {parts[1]}")
    has_replace = bool(re.search(r"(?m)^\s*replace(?:\s|\()", text))
    main = source_dir / "main.go"
    root_main = False
    if main.is_file() and not main.is_symlink():
        with suppress(OSError, UnicodeDecodeError):
            root_main = bool(
                re.search(r"(?m)^\s*package\s+main\s*$", main.read_text(encoding="utf-8")[:1_000_000])
            )
    valid_entrypoint = bool(SAFE_ENTRYPOINT.fullmatch(entrypoint))
    result.update(
        {
            "track": "go-cli",
            "supported": bool(module_match)
            and valid_entrypoint
            and root_main
            and not has_replace
            and (not requirements or (source_dir / "go.sum").is_file()),
            "project_name": entrypoint or None,
            "project_version": None,
            "requires_go": version_match.group(1) if version_match else None,
            "module": module or None,
            "entrypoints": {entrypoint: "."} if valid_entrypoint and root_main else {},
            "dependencies": requirements[:250],
            "has_go_sum": (source_dir / "go.sum").is_file(),
            "has_replace_directive": has_replace,
            "build_target": ".",
            "install_strategy": "go-build-offline-module-proxy",
            "lockfiles": ["go.sum"] if (source_dir / "go.sum").is_file() else [],
        }
    )
    return result


def collect_important_evidence(source_dir: Path) -> list[dict[str, str]]:
    paths = []
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source_dir)
        if path.name in IMPORTANT_NAMES or rel.parts[:2] == (".github", "workflows"):
            paths.append(path)
    evidence = []
    for path in sorted(paths)[:80]:
        try:
            text = clean_text(path.read_text(encoding="utf-8", errors="replace"), 24_000)
        except OSError:
            continue
        evidence.append({"path": path.relative_to(source_dir).as_posix(), "excerpt": text})
    return evidence


RISK_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "CRITICAL",
        "destructive_root",
        re.compile(r"\brm\s+(?:-[A-Za-z]*r[A-Za-z]*f|-[A-Za-z]*f[A-Za-z]*r)\s+/(?:\s|$)"),
    ),
    ("CRITICAL", "disk_destruction", re.compile(r"\b(?:mkfs(?:\.\w+)?|dd\s+if=)\b")),
    ("CRITICAL", "reverse_shell", re.compile(r"(?:/dev/tcp/|\bnc\s+[^\n]*\s-e\s|bash\s+-i\s+[^\n]*>&)")),
    ("CRITICAL", "cryptominer", re.compile(r"\b(?:xmrig|stratum\+tcp|cryptonight)\b", re.I)),
    ("HIGH", "docker_socket", re.compile(r"(?:/var/run/docker\.sock|DOCKER_HOST\s*=)")),
    (
        "HIGH",
        "credential_access",
        re.compile(r"(?:~/\.ssh|/\.ssh/id_|~/\.aws/credentials|/root/\.config/gh)"),
    ),
    ("HIGH", "pipe_to_shell", re.compile(r"\b(?:curl|wget)\b[^\n|]{0,500}\|\s*(?:sudo\s+)?(?:sh|bash)\b")),
    ("HIGH", "persistence", re.compile(r"\b(?:crontab|systemctl\s+enable|/etc/cron\.)\b")),
    ("HIGH", "privilege_escalation", re.compile(r"\b(?:sudo\s+|setcap\s+|chmod\s+(?:-R\s+)?777\s+/)")),
    (
        "HIGH",
        "shell_execution",
        re.compile(r"exec\.Command(?:Context)?\s*\(\s*[\"'](?:/bin/)?(?:sh|bash)[\"']\s*,\s*[\"']-c[\"']"),
    ),
    (
        "MEDIUM",
        "encoded_execution",
        re.compile(r"\bbase64\s+(?:-d|--decode)[^\n|]{0,300}\|\s*(?:sh|bash|python)\b"),
    ),
]

LICENSE_RESTRICTION = re.compile(
    r"(?:commons clause|business source license|non[- ]commercial (?:use )?only|"
    r"no commercial (?:use|purpose)|no derivatives)",
    re.I,
)

EXECUTION_RELEVANT = {
    ".sh",
    ".bash",
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".go",
    ".ps1",
    ".yml",
    ".yaml",
    ".toml",
    ".cfg",
    ".ini",
}


def assess_risk(source_dir: Path, repository: dict[str, Any]) -> RiskReport:
    findings: list[RiskFinding] = []
    restrictive_license = False
    files_scanned = 0
    bytes_scanned = 0
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source_dir).as_posix()
        try:
            head = path.read_bytes()[:4]
        except OSError:
            continue
        if head == b"\x7fELF" or head[:2] == b"MZ":
            findings.append(
                RiskFinding(
                    "HIGH", "opaque_executable", rel, None, "compiled executable stored in source tree"
                )
            )
            continue
        license_document = path.name.casefold().startswith(("license", "copying", "readme"))
        if (
            path.suffix.lower() not in EXECUTION_RELEVANT
            and path.name
            not in {
                "Dockerfile",
                "Makefile",
                "configure",
                "package.json",
            }
            and not license_document
        ):
            continue
        try:
            raw = path.read_bytes()[:1_000_000]
        except OSError:
            continue
        text = raw.decode("utf-8", errors="replace")
        if license_document:
            match = LICENSE_RESTRICTION.search(text)
            if match:
                restrictive_license = True
                findings.append(
                    RiskFinding(
                        "MEDIUM",
                        "restrictive_license_terms",
                        rel,
                        text.count("\n", 0, match.start()) + 1,
                        clean_text(match.group(0), 300),
                    )
                )
        files_scanned += 1
        bytes_scanned += len(raw)
        if b"\x00" in raw:
            continue
        for severity, category, pattern in RISK_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                evidence = clean_text(
                    text[max(0, match.start() - 80) : match.end() + 120].replace("\n", " "), 300
                )
                effective_severity = (
                    "MEDIUM"
                    if category == "privilege_escalation" and rel.startswith(".github/workflows/")
                    else severity
                )
                findings.append(RiskFinding(effective_severity, category, rel, line, evidence))
                if len(findings) >= 200:
                    break
            if len(findings) >= 200:
                break
        if len(findings) >= 200:
            break

    severities = {finding.severity for finding in findings}
    license_id = str(repository.get("license") or "")
    age_days = _age_days(repository.get("created_at"))
    stars = int(repository.get("stars") or 0)
    if "CRITICAL" in severities:
        classification = TrustClass.REJECTED
        rationale = "Static screening found behavior that is incompatible with execution."
    elif "HIGH" in severities:
        classification = TrustClass.REVIEW
        rationale = "Static screening found high-risk behavior requiring human review."
    elif not license_id or license_id in {"NOASSERTION", "OTHER"}:
        classification = TrustClass.INSUFFICIENT
        rationale = "No recognized open-source license was established."
    elif restrictive_license:
        classification = TrustClass.INSUFFICIENT
        rationale = "Checked-in licensing text contains an apparent use restriction requiring review."
    elif repository.get("archived") or repository.get("fork"):
        classification = TrustClass.RESTRICTED
        rationale = "The repository is archived or a fork; provenance needs additional review."
    elif age_days >= 365 and stars >= 1_000 and int(repository.get("contributors_sample") or 0) >= 3:
        classification = TrustClass.TRUSTED
        rationale = "Long-lived repository, recognized license, multiple sampled contributors, substantial public use, and no high-risk static finding."
    else:
        classification = TrustClass.RESTRICTED
        rationale = "No high-risk static finding, but V1 provenance thresholds for trusted execution were not all met."
    return RiskReport(
        classification=classification,
        rationale=rationale,
        findings=findings,
        files_scanned=files_scanned,
        bytes_scanned=bytes_scanned,
        limitations=[
            "Pattern-based static screening cannot establish that code is safe.",
            "Dependencies are not executed during screening and require their own provenance controls.",
            "Generated, encrypted, or runtime-downloaded behavior may not be visible.",
        ],
    )


def _age_days(value: object) -> int:
    if not isinstance(value, str):
        return 0
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, (datetime.now(UTC) - created).days)
