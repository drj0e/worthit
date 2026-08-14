from __future__ import annotations

import json
import math
import os
import re
import subprocess
import urllib.parse
import urllib.request
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

from .inspect import (
    COMMIT,
    INSPECTION_REVISION,
    MAX_ARCHIVE_BYTES,
    MAX_EXTRACT_BYTES,
    MAX_FILES,
    OPEN_SOURCE_SPDX,
    GitHubClient,
    assess_risk,
    assess_v1_requirements,
    clean_text,
    inspect_repository,
    parse_repo_url,
    refresh_repository_inspection,
)
from .models import TrustClass, atomic_json, jsonable
from .pipeline import evaluate_repository
from .planning import model_cost
from .runner import redact
from .site import build_site

TRENDING_FEEDS: tuple[tuple[str, str], ...] = (
    ("overall", "https://github.com/trending?since=daily"),
    ("python", "https://github.com/trending/python?since=daily"),
    ("typescript", "https://github.com/trending/typescript?since=daily"),
    ("javascript", "https://github.com/trending/javascript?since=daily"),
    ("rust", "https://github.com/trending/rust?since=daily"),
    ("go", "https://github.com/trending/go?since=daily"),
)
SEARCH_TOPICS = ("artificial-intelligence", "llm", "mcp", "developer-tools")
SUPPORTED_LANGUAGES = {"python", "typescript", "javascript", "go"}
AI_TERMS = {
    "ai",
    "agent",
    "agents",
    "artificial intelligence",
    "coding agent",
    "embedding",
    "inference",
    "llm",
    "llms",
    "local ai",
    "mcp",
    "model",
    "models",
    "multimodal",
    "rag",
    "speech to text",
    "stt",
    "text to speech",
    "tts",
}
DEV_TERMS = {
    "automation",
    "benchmark",
    "cli",
    "code",
    "compiler",
    "database",
    "developer",
    "devtool",
    "formatter",
    "git",
    "github",
    "json",
    "lint",
    "mcp",
    "runtime",
    "terminal",
    "test",
    "tool",
}
LOW_VALUE = re.compile(
    r"(?:awesome[- ]list|boilerplate|course exercises?|prompt collection|starter template|"
    r"tutorial|thin wrapper|chatgpt wrapper)",
    re.I,
)
PRIORITY_WEIGHTS = {
    "relevance": 25,
    "trending_velocity": 20,
    "testability": 15,
    "utility": 10,
    "community_interest": 10,
    "recency": 10,
    "novelty": 5,
    "coverage_gap": 5,
}
QUALIFICATION_REVISION = 5
DISCOVERY_QUERY_REVISION = 2


class DiscoveryProvider(Protocol):
    name: str
    errors: list[str]

    def discover(self, *, now: datetime, limit: int) -> list[dict[str, Any]]: ...


class _TrendingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_article = False
        self.in_heading = False
        self.in_daily = False
        self.repository: str | None = None
        self.daily_text: list[str] = []
        self.results: list[tuple[str, int | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "article" and "Box-row" in classes:
            self.in_article = True
            self.repository = None
            self.daily_text = []
        elif self.in_article and tag == "h2":
            self.in_heading = True
        elif self.in_article and self.in_heading and tag == "a" and self.repository is None:
            href = attributes.get("href") or ""
            with suppress(ValueError):
                self.repository = parse_repo_url("https://github.com" + href).slug
        elif self.in_article and tag == "span" and "float-sm-right" in classes:
            self.in_daily = True

    def handle_data(self, data: str) -> None:
        if self.in_daily:
            self.daily_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self.in_heading = False
        elif tag == "span" and self.in_daily:
            self.in_daily = False
        elif tag == "article" and self.in_article:
            text = " ".join(self.daily_text)
            match = re.search(r"([0-9][0-9,]*)\s+stars?\s+today", text, re.I)
            if self.repository:
                self.results.append(
                    (self.repository, int(match.group(1).replace(",", "")) if match else None)
                )
            self.in_article = False


def parse_trending(html_text: str) -> list[tuple[str, int | None]]:
    parser = _TrendingParser()
    parser.feed(html_text)
    return parser.results


class GitHubDiscoveryProvider:
    name = "github"

    def __init__(self, cache_dir: Path, *, cache_hours: int = 6) -> None:
        self.cache_path = cache_dir / "github-discovery.json"
        self.cache_hours = cache_hours
        self.errors: list[str] = []

    def discover(self, *, now: datetime, limit: int) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("discovery limit must be between 1 and 200")
        cached = self._read_cache()
        if cached and now - cached[0] <= timedelta(hours=self.cache_hours):
            return cached[1][:limit]
        try:
            candidates = self._discover_fresh(now, limit)
        except (OSError, RuntimeError, ValueError) as error:
            if cached and now - cached[0] <= timedelta(hours=48):
                self.errors.append(
                    redact(f"live discovery failed; reused cache: {clean_text(str(error), 500)}")
                )
                return cached[1][:limit]
            raise
        atomic_json(
            self.cache_path,
            {
                "schema_version": 1,
                "query_revision": DISCOVERY_QUERY_REVISION,
                "fetched_at": now.isoformat(),
                "candidates": candidates,
            },
        )
        return candidates

    def _read_cache(self) -> tuple[datetime, list[dict[str, Any]]] | None:
        if not self.cache_path.is_file():
            return None
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(str(value["fetched_at"]))
            candidates = value["candidates"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if value.get("schema_version") != 1 or value.get("query_revision") != DISCOVERY_QUERY_REVISION:
            return None
        if fetched.tzinfo is None or not isinstance(candidates, list):
            return None
        if any(not isinstance(item, dict) for item in candidates):
            return None
        return fetched.astimezone(UTC), candidates

    def _discover_fresh(self, now: datetime, limit: int) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for label, url in TRENDING_FEEDS:
            try:
                for repository, stars_today in self._trending(url):
                    self._merge_signal(found, repository, f"github-trending:{label}", stars_today, None)
            except (OSError, ValueError) as error:
                self.errors.append(redact(f"trending {label}: {clean_text(str(error), 300)}"))

        client = GitHubClient()
        pushed_after = (now - timedelta(days=21)).date().isoformat()
        for topic in SEARCH_TOPICS:
            query = f"topic:{topic} stars:>25 pushed:>={pushed_after} archived:false fork:false"
            path = "/search/repositories?" + urllib.parse.urlencode(
                {"q": query, "sort": "stars", "order": "desc", "per_page": "50"}
            )
            try:
                response = client.json(path)
                items = response.get("items") if isinstance(response, dict) else None
                if not isinstance(items, list):
                    raise ValueError("GitHub repository search returned no item list")
                for item in items:
                    if not isinstance(item, dict) or not isinstance(item.get("full_name"), str):
                        continue
                    self._merge_signal(found, item["full_name"], f"github-search:{topic}", None, item)
            except (OSError, RuntimeError, ValueError) as error:
                self.errors.append(redact(f"search {topic}: {clean_text(str(error), 300)}"))

        if not found:
            raise RuntimeError("all GitHub discovery sources failed")
        ranked = sorted(
            found.values(),
            key=lambda item: (
                len(item["sources"]),
                int(item.get("stars_today") or 0),
                int((item.get("api") or {}).get("stargazers_count") or 0),
            ),
            reverse=True,
        )
        candidates: list[dict[str, Any]] = []
        for raw in ranked:
            if len(candidates) >= limit:
                break
            try:
                api = raw.get("api")
                if not isinstance(api, dict):
                    api = client.json(f"/repos/{raw['repository']}")
                if not isinstance(api, dict) or bool(api.get("private")):
                    continue
                default_branch = str(api.get("default_branch") or "")
                commit = client.json(
                    f"/repos/{api['full_name']}/commits/{urllib.parse.quote(default_branch, safe='')}"
                )
                if not isinstance(commit, dict) or not COMMIT.fullmatch(str(commit.get("sha") or "")):
                    continue
                candidates.append(
                    _candidate_from_api(
                        api,
                        commit,
                        sorted(raw["sources"]),
                        raw.get("stars_today"),
                        now,
                    )
                )
            except (KeyError, OSError, RuntimeError, ValueError) as error:
                self.errors.append(
                    redact(f"metadata {raw.get('repository', 'unknown')}: {clean_text(str(error), 300)}")
                )
        if not candidates:
            raise RuntimeError("GitHub discovery found repositories but could not resolve exact commits")
        return candidates

    def _trending(self, url: str) -> list[tuple[str, int | None]]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html",
                "User-Agent": "worthit-lab/0.1 (+https://github.com/drj0e/worthit)",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if int(response.headers.get("Content-Length", "0") or 0) > 5_000_000:
                raise ValueError("GitHub Trending response is unexpectedly large")
            raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError("GitHub Trending response exceeded 5 MB")
        return parse_trending(raw.decode("utf-8", errors="replace"))

    @staticmethod
    def _merge_signal(
        found: dict[str, dict[str, Any]],
        repository: str,
        source: str,
        stars_today: int | None,
        api: dict[str, Any] | None,
    ) -> None:
        ref = parse_repo_url(f"https://github.com/{repository}")
        key = ref.slug.casefold()
        item = found.setdefault(
            key,
            {"repository": ref.slug, "sources": set(), "stars_today": None, "api": None},
        )
        item["sources"].add(source)
        if stars_today is not None:
            item["stars_today"] = max(int(item.get("stars_today") or 0), stars_today)
        if api is not None:
            item["api"] = api


def _candidate_from_api(
    repository: dict[str, Any],
    commit: dict[str, Any],
    sources: list[str],
    stars_today: int | None,
    now: datetime,
) -> dict[str, Any]:
    full_name = str(repository.get("full_name") or "")
    ref = parse_repo_url(f"https://github.com/{full_name}")
    sha = str(commit.get("sha") or "").casefold()
    if not COMMIT.fullmatch(sha) or len(sha) != 40:
        raise ValueError("GitHub commit response did not contain a full SHA")
    license_value = repository.get("license")
    license_id = str(license_value.get("spdx_id") or "") if isinstance(license_value, dict) else ""
    owner = repository.get("owner")
    topics = repository.get("topics")
    commit_data = commit.get("commit")
    committer = commit_data.get("committer") if isinstance(commit_data, dict) else None
    return {
        "repository": ref.slug,
        "owner": ref.owner,
        "name": ref.name,
        "url": ref.url,
        "description": redact(clean_text(str(repository.get("description") or ""), 500)),
        "primary_language": clean_text(str(repository.get("language") or ""), 80),
        "topics": [clean_text(str(topic), 80) for topic in topics[:30]] if isinstance(topics, list) else [],
        "license": license_id,
        "created_at": repository.get("created_at"),
        "updated_at": repository.get("updated_at"),
        "pushed_at": repository.get("pushed_at"),
        "commit_date": committer.get("date") if isinstance(committer, dict) else None,
        "stars": max(0, int(repository.get("stargazers_count") or 0)),
        "forks": max(0, int(repository.get("forks_count") or 0)),
        "stars_today": stars_today,
        "size_kb": max(0, int(repository.get("size") or 0)),
        "archived": bool(repository.get("archived")),
        "disabled": bool(repository.get("disabled")),
        "fork": bool(repository.get("fork")),
        "owner_type": clean_text(str(owner.get("type") or ""), 40) if isinstance(owner, dict) else "",
        "discovery_sources": sources,
        "discovered_at": now.isoformat(),
        "current_commit_sha": sha,
        "current_release": None,
        "readme_reference": f"{ref.url}/tree/{sha}#readme",
        "candidate_status": "DISCOVERED",
        "category": _category(repository),
        "testability": "PENDING",
        "risk_classification": TrustClass.INSUFFICIENT.value,
        "priority": {},
        "previous_worthit_run": None,
    }


def _category(candidate: dict[str, Any]) -> str:
    if (candidate.get("environment") or {}).get("track") == "python-library":
        return "developer library"
    text = " ".join(
        [
            str(candidate.get("name") or ""),
            str(candidate.get("description") or ""),
            *[str(value) for value in candidate.get("topics", [])],
        ]
    ).casefold()
    categories = (
        (("coding agent", "code agent", "ai coding"), "coding agent"),
        (("model context protocol", "mcp"), "MCP tool/server"),
        (("retrieval augmented", "rag", "vector search"), "RAG/search"),
        (("text to speech", "tts"), "TTS"),
        (("speech to text", "stt", "transcription"), "STT"),
        (("image generation", "diffusion"), "image generation"),
        (("browser automation", "headless browser"), "browser automation"),
        (("benchmark", "evaluation", "evals"), "evaluation/benchmarking"),
        (("runtime", "gateway", "proxy"), "infrastructure/runtime"),
        (("formatter", "linter", "developer", "devtool", "git", "code"), "developer productivity"),
    )
    return next((label for terms, label in categories if _term_hits(text, set(terms))), "CLI utility")


def _term_hits(text: str, terms: set[str]) -> int:
    tokens = set(re.findall(r"[a-z0-9]+", text))
    return sum(term in text if " " in term else term in tokens for term in terms)


def score_candidate(
    candidate: dict[str, Any], covered_categories: set[str], *, now: datetime
) -> dict[str, Any]:
    text = " ".join(
        [
            str(candidate.get("name") or ""),
            str(candidate.get("description") or ""),
            *[str(value) for value in candidate.get("topics", [])],
        ]
    ).casefold()
    ai_hits = _term_hits(text, AI_TERMS)
    dev_hits = _term_hits(text, DEV_TERMS)
    language = str(candidate.get("primary_language") or "").casefold()
    relevance = min(100, 20 * (language in SUPPORTED_LANGUAGES) + 35 * bool(dev_hits) + 45 * bool(ai_hits))
    pushed_days = _days_since(candidate.get("pushed_at"), now)
    age_days = _days_since(candidate.get("created_at"), now)
    stars = max(0, int(candidate.get("stars") or 0))
    forks = max(0, int(candidate.get("forks") or 0))
    stars_today = candidate.get("stars_today")
    velocity = min(100, round(math.sqrt(max(0, int(stars_today or 0))) * 10))
    community = min(100, round(math.log10(stars + 1) * 22))
    recency = (
        100
        if pushed_days <= 7
        else 80
        if pushed_days <= 30
        else 60
        if pushed_days <= 90
        else 30
        if pushed_days <= 365
        else 0
    )
    novelty = 100 if age_days <= 90 else 70 if age_days <= 365 else 40 if age_days <= 1095 else 15
    low_value = bool(LOW_VALUE.search(text))
    utility = (
        10
        if low_value
        else min(100, 40 + 20 * bool(candidate.get("description")) + 10 * min(4, ai_hits + dev_hits))
    )
    qualification = candidate.get("qualification") or {}
    testability_gate = (qualification.get("gates") or {}).get("testability") or {}
    testability = (
        100
        if testability_gate.get("passed") is True
        else 0
        if qualification.get("revision") == QUALIFICATION_REVISION
        and qualification.get("inspection_revision") == INSPECTION_REVISION
        else 60
        if language in SUPPORTED_LANGUAGES
        else 0
    )
    category = str(candidate.get("category") or "CLI utility")
    coverage = 60 if category not in covered_categories else 40
    components = {
        "relevance": relevance,
        "trending_velocity": velocity,
        "testability": testability,
        "utility": utility,
        "community_interest": community,
        "recency": recency,
        "novelty": novelty,
        "coverage_gap": coverage,
    }
    total = round(sum(components[name] * weight for name, weight in PRIORITY_WEIGHTS.items()) / 100)
    interestingness = round(
        velocity * 0.25 + community * 0.2 + recency * 0.2 + utility * 0.25 + novelty * 0.1
    )
    sources = candidate.get("discovery_sources")
    source_count = len(sources) if isinstance(sources, list) else 0
    corroborated = source_count >= 2 or (stars >= 10_000 and forks >= 100 and age_days >= 365)
    license_id = str(candidate.get("license") or "")
    metadata_gates: dict[str, dict[str, object]] = {
        "relevance": {
            "passed": relevance >= 55,
            "reason": f"{ai_hits} AI and {dev_hits} developer-tool signals; score {relevance}/100.",
        },
        "interestingness": {
            "passed": interestingness >= 50 and corroborated and not low_value,
            "reason": (
                f"score {interestingness}/100 across {source_count} discovery signals; "
                f"corroborated={corroborated}, low-value-pattern={low_value}; novelty is a "
                "metadata heuristic, not a code-originality finding."
            ),
        },
        "provenance": {
            "passed": not bool(
                candidate.get("fork") or candidate.get("archived") or candidate.get("disabled")
            ),
            "reason": "GitHub reports an active, non-fork public repository."
            if not bool(candidate.get("fork") or candidate.get("archived") or candidate.get("disabled"))
            else "GitHub reports a fork, archive, or disabled repository.",
        },
        "license": {
            "passed": license_id in OPEN_SOURCE_SPDX,
            "reason": f"GitHub detected {license_id or 'no recognized license'}; checked-in terms are inspected before execution.",
        },
        "description_present": {
            "passed": bool(candidate.get("description")),
            "reason": "Repository description is present; README quality is checked during deep qualification.",
        },
        "testability": {
            "passed": language in SUPPORTED_LANGUAGES,
            "reason": f"{candidate.get('primary_language') or 'unknown language'} is {'eligible for' if language in SUPPORTED_LANGUAGES else 'outside'} a current CLI track; manifests still require inspection.",
        },
        "resource_feasibility": {
            "passed": 20 <= int(candidate.get("size_kb") or 0) <= MAX_ARCHIVE_BYTES // 1024,
            "reason": (
                f"GitHub reports {int(candidate.get('size_kb') or 0)} KiB; the metadata screen "
                f"accepts 20-{MAX_ARCHIVE_BYTES // 1024} KiB before measuring the archive."
            ),
        },
        "activity": {
            "passed": pushed_days <= 365,
            "reason": f"Last code push was {pushed_days} day(s) ago.",
        },
    }
    interest_reasons = []
    if stars_today:
        interest_reasons.append(f"GitHub Trending reported {int(stars_today)} stars today.")
    if source_count >= 2:
        interest_reasons.append(f"It appeared in {source_count} GitHub discovery feeds/searches.")
    elif corroborated:
        interest_reasons.append(
            f"Its mature public footprint is {stars} stars and {forks} forks over {age_days} days."
        )
    interest_reasons.append(
        f"Metadata contains {ai_hits} AI and {dev_hits} developer-tool signals; "
        f"interest score {interestingness}/100."
    )
    candidate["priority"] = {
        "total": total,
        "components": components,
        "weights": PRIORITY_WEIGHTS,
        "interestingness": interestingness,
        "reasons": interest_reasons
        + [
            f"Classified as {category}; coverage is only a tie-breaker, not a quota.",
            "Stars and trending velocity are discovery signals, not quality evidence.",
        ],
    }
    candidate["metadata_gates"] = metadata_gates
    candidate["metadata_qualified"] = (
        all(bool(gate["passed"]) for gate in metadata_gates.values()) and total >= 55
    )
    return candidate


def _days_since(value: object, now: datetime) -> int:
    if not isinstance(value, str):
        return 100_000
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 100_000
    if timestamp.tzinfo is None:
        return 100_000
    return max(0, (now - timestamp.astimezone(UTC)).days)


def _review_index(reviews_root: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if not reviews_root.exists():
        return index
    for path in reviews_root.rglob("review.json"):
        try:
            review = json.loads(path.read_text(encoding="utf-8"))
            repository = str(review["repository"])
            parse_repo_url(f"https://github.com/{repository}")
            commit = str(review["commit_sha"])
            tested_at = str(review["tested_at"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        item = index.setdefault(repository.casefold(), {"commits": set(), "latest": None})
        item["commits"].add(commit.casefold())
        latest = item["latest"]
        if not isinstance(latest, dict) or tested_at > str(latest.get("tested_at") or ""):
            item["latest"] = {
                "commit_sha": commit,
                "tested_at": tested_at,
                "score": (review.get("score") or {}).get("overall"),
                "verdict": (review.get("score") or {}).get("verdict"),
            }
    return index


def _load_backlog(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "updated_at": None, "candidates": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported candidate backlog schema")
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or any(not isinstance(item, dict) for item in candidates):
        raise ValueError("candidate backlog must contain objects")
    for candidate in candidates:
        ref = parse_repo_url(str(candidate.get("url") or ""))
        if candidate.get("repository") != ref.slug:
            raise ValueError("candidate backlog repository does not match its URL")
        if not COMMIT.fullmatch(str(candidate.get("current_commit_sha") or "")):
            raise ValueError("candidate backlog contains an invalid commit")
    return value


def _merge_backlog(
    backlog: dict[str, Any], discovered: list[dict[str, Any]], *, now: datetime
) -> list[dict[str, Any]]:
    existing = {
        str(item["repository"]).casefold(): item
        for item in backlog["candidates"]
        if isinstance(item.get("repository"), str)
    }
    for incoming in discovered:
        key = str(incoming["repository"]).casefold()
        previous = existing.get(key)
        same_commit = previous is not None and previous.get("current_commit_sha") == incoming.get(
            "current_commit_sha"
        )
        events = list(previous.get("discovery_events", [])) if previous else []
        event = {
            "discovered_at": now.isoformat(),
            "commit_sha": incoming["current_commit_sha"],
            "stars": incoming["stars"],
            "stars_today": incoming["stars_today"],
            "sources": incoming["discovery_sources"],
        }
        if not events or events[-1] != event:
            events.append(event)
        merged = {**incoming, "discovery_events": events[-90:], "last_discovered_at": now.isoformat()}
        if previous:
            merged["discovered_at"] = previous.get("discovered_at", incoming["discovered_at"])
        if same_commit and previous:
            for name in (
                "qualification",
                "risk_classification",
                "testability",
                "candidate_status",
                "selected_at",
                "selection_date",
                "evaluation_attempts",
                "retry_exhausted",
            ):
                if name in previous:
                    merged[name] = previous[name]
        existing[key] = merged
    return sorted(existing.values(), key=lambda item: str(item["repository"]).casefold())


def _deep_qualify(
    candidate: dict[str, Any], *, work_root: Path, reviewed_commits: set[str], now: datetime
) -> dict[str, Any]:
    ref = parse_repo_url(str(candidate["url"]))
    sha = str(candidate["current_commit_sha"])
    run_dir = (work_root / "runs" / f"{ref.owner}--{ref.name}" / sha).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    repository_path = run_dir / "repository.json"
    if repository_path.exists() and (run_dir / "source").is_dir():
        repository = json.loads(repository_path.read_text(encoding="utf-8"))
        if repository.get("url") != ref.url or repository.get("commit_sha") != sha:
            raise ValueError("cached deep-inspection identity mismatch")
        repository = refresh_repository_inspection(repository, run_dir)
    else:
        repository = inspect_repository(ref.url, run_dir, sha)
    risk = jsonable(assess_risk(run_dir / "source", repository))
    atomic_json(run_dir / "risk.json", risk)
    environment = repository.get("environment")
    if not isinstance(environment, dict):
        environment = {}
    requirements = assess_v1_requirements(repository)
    repository["v1_requirements"] = requirements
    atomic_json(repository_path, repository)
    readme = str(repository.get("readme") or "")
    release = repository.get("release")
    archive_bytes = int(repository.get("archive_bytes") or 0)
    source_bytes = int(repository.get("source_bytes") or 0)
    source_files = int(repository.get("source_files") or 0)
    candidate.update(
        {
            "description": redact(
                clean_text(str(repository.get("description") or candidate.get("description", "")), 500)
            ),
            "primary_language": repository.get("primary_language") or candidate.get("primary_language", ""),
            "topics": repository.get("topics") or candidate.get("topics", []),
            "license": repository.get("license") or candidate.get("license", ""),
            "stars": repository.get("stars", candidate.get("stars", 0)),
            "forks": repository.get("forks", candidate.get("forks", 0)),
            "created_at": repository.get("created_at"),
            "updated_at": repository.get("updated_at"),
            "pushed_at": repository.get("pushed_at"),
            "current_release": release if isinstance(release, dict) else None,
            "readme_reference": f"{ref.url}/blob/{sha}/{repository.get('readme_path') or 'README'}",
            "testability": "SUPPORTED" if environment.get("supported") else "UNSUPPORTED",
            "requirements_classification": requirements["classification"],
            "risk_classification": risk["classification"],
            "category": _category(repository),
        }
    )
    gates = {
        "documentation": {
            "passed": len(readme.strip()) >= 200,
            "reason": f"README contained {len(readme.encode('utf-8'))} bytes of inspectable text.",
        },
        "testability": {
            "passed": bool(environment.get("supported")),
            "reason": (
                f"Detected {environment.get('track') or 'no V1 execution track'}; "
                f"supported={bool(environment.get('supported'))}."
            ),
        },
        "risk": {
            "passed": risk["classification"] == TrustClass.TRUSTED.value,
            "reason": str(risk["rationale"]),
        },
        "v1_requirements": {
            "passed": bool(requirements["passed"]),
            "classification": requirements["classification"],
            "reason": "; ".join(str(reason) for reason in requirements["reasons"]),
            "indicators": requirements["indicators"],
        },
        "resource_feasibility": {
            "passed": (
                0 < archive_bytes <= MAX_ARCHIVE_BYTES
                and 0 < source_bytes <= MAX_EXTRACT_BYTES
                and 0 < source_files <= MAX_FILES
            ),
            "reason": (
                f"Measured {archive_bytes} archive bytes, {source_bytes} extracted bytes, and "
                f"{source_files} files against V1 limits of {MAX_ARCHIVE_BYTES}, "
                f"{MAX_EXTRACT_BYTES}, and {MAX_FILES}."
            ),
        },
        "already_tested": {
            "passed": sha.casefold() not in reviewed_commits,
            "reason": "This exact commit has not been published."
            if sha.casefold() not in reviewed_commits
            else "This exact commit already has a published WorthIt review.",
        },
    }
    candidate["qualification"] = {
        "revision": QUALIFICATION_REVISION,
        "inspection_revision": INSPECTION_REVISION,
        "inspected_at": now.isoformat(),
        "environment_track": environment.get("track"),
        "risk_findings": len(risk.get("findings", [])),
        "gates": gates,
        "passed": bool(candidate.get("metadata_qualified"))
        and all(gate["passed"] for gate in gates.values()),
    }
    candidate["candidate_status"] = "QUALIFIED" if candidate["qualification"]["passed"] else "SKIPPED"
    return candidate


def run_daily(
    *,
    provider: DiscoveryProvider,
    backlog_path: Path,
    hunts_root: Path,
    work_root: Path,
    reviews_root: Path,
    site_dir: Path,
    discovery_limit: int = 80,
    qualify_limit: int = 10,
    daily_limit: int = 5,
    max_retries: int = 2,
    max_llm_cost_per_repo: float = 5.0,
    max_total_daily_llm_cost: float = 25.0,
    execute: bool = False,
    allow_runc: bool = False,
    base_path: str = "/",
    site_url: str = "http://localhost:8000",
    now: datetime | None = None,
    run_date: date | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    operating_date = run_date or now.date()
    if abs((operating_date - now.date()).days) > 1:
        raise ValueError("daily operating date must be today or an adjacent UTC date")
    if not 1 <= daily_limit <= 5:
        raise ValueError("daily test limit must be between 1 and 5")
    if not daily_limit <= qualify_limit <= 25:
        raise ValueError("qualification limit must be between the daily limit and 25")
    if not 1 <= max_retries <= 10:
        raise ValueError("maximum retries must be between 1 and 10")
    if (
        not math.isfinite(max_llm_cost_per_repo)
        or not math.isfinite(max_total_daily_llm_cost)
        or max_llm_cost_per_repo <= 0
        or max_total_daily_llm_cost < max_llm_cost_per_repo
    ):
        raise ValueError("LLM cost limits must be positive and the daily limit must cover one repository")
    if execute and not allow_runc:
        raise ValueError("--execute requires --allow-runc to acknowledge the documented residual risk")
    discovered = provider.discover(now=now, limit=discovery_limit)
    backlog = _load_backlog(backlog_path)
    candidates = _merge_backlog(backlog, discovered, now=now)
    reviews = _review_index(reviews_root)
    covered_categories = {
        str(review.get("category") or "")
        for path in reviews_root.rglob("review.json")
        if (review := _safe_json(path)) is not None
    }
    for candidate in candidates:
        candidate["category"] = _category(candidate)
        reviewed = reviews.get(str(candidate["repository"]).casefold())
        candidate["previous_worthit_run"] = reviewed.get("latest") if reviewed else None
        score_candidate(candidate, covered_categories, now=now)
        attempts = max(0, int(candidate.get("evaluation_attempts") or 0))
        candidate["retry_exhausted"] = attempts >= max_retries
        if reviewed and str(candidate["current_commit_sha"]).casefold() in reviewed["commits"]:
            candidate["candidate_status"] = "ALREADY_TESTED"

    metadata_eligible = sorted(
        (
            candidate
            for candidate in candidates
            if candidate.get("metadata_qualified")
            and candidate.get("candidate_status") != "ALREADY_TESTED"
            and not candidate.get("retry_exhausted")
            and not _qualification_blocks_retry(candidate, now)
        ),
        key=lambda item: int((item.get("priority") or {}).get("total") or 0),
        reverse=True,
    )
    already_qualified = [
        candidate
        for candidate in metadata_eligible
        if (candidate.get("qualification") or {}).get("revision") == QUALIFICATION_REVISION
        and (candidate.get("qualification") or {}).get("inspection_revision") == INSPECTION_REVISION
        and (candidate.get("qualification") or {}).get("passed") is True
    ]
    needs_qualification = [candidate for candidate in metadata_eligible if candidate not in already_qualified]
    deep_checked: list[dict[str, Any]] = []
    for candidate in needs_qualification[:qualify_limit]:
        candidate["candidate_status"] = "QUALIFYING"
        reviewed = reviews.get(str(candidate["repository"]).casefold())
        reviewed_commits = reviewed["commits"] if reviewed else set()
        try:
            _deep_qualify(candidate, work_root=work_root, reviewed_commits=reviewed_commits, now=now)
            score_candidate(candidate, covered_categories, now=now)
        except (OSError, RuntimeError, ValueError) as error:
            candidate["candidate_status"] = "BLOCKED_TRANSIENT_QUALIFICATION"
            candidate["qualification"] = {
                "revision": QUALIFICATION_REVISION,
                "inspection_revision": INSPECTION_REVISION,
                "inspected_at": now.isoformat(),
                "passed": False,
                "retryable_error": True,
                "retry_after": (now + timedelta(hours=6)).isoformat(),
                "error": redact(clean_text(str(error), 1_000)),
            }
        deep_checked.append(candidate)

    eligible = sorted(
        already_qualified
        + [candidate for candidate in deep_checked if (candidate.get("qualification") or {}).get("passed")],
        key=lambda item: int((item.get("priority") or {}).get("total") or 0),
        reverse=True,
    )
    date_text = operating_date.isoformat()
    selected_today = {
        str(candidate["repository"]).casefold()
        for candidate in candidates
        if _selection_date(candidate) == date_text
    }
    retryable = [
        candidate
        for candidate in eligible
        if str(candidate["repository"]).casefold() in selected_today
        and candidate.get("candidate_status") != "PUBLISHED"
    ]
    remaining = max(0, daily_limit - len(selected_today))
    selected = (
        retryable
        + [
            candidate
            for candidate in eligible
            if str(candidate["repository"]).casefold() not in selected_today
        ][:remaining]
    )
    for candidate in selected:
        candidate["candidate_status"] = "SELECTED"
        if _selection_date(candidate) != date_text:
            candidate["selected_at"] = now.isoformat()
            candidate["selection_date"] = date_text

    backlog.update({"updated_at": now.isoformat(), "candidates": candidates})
    atomic_json(backlog_path, backlog)
    report_path = hunts_root / f"{date_text}.json"
    previous_report = _safe_json(report_path)
    outcomes = (
        _execute_candidates(
            selected,
            backlog=backlog,
            backlog_path=backlog_path,
            previous_report=previous_report,
            work_root=work_root,
            reviews_root=reviews_root,
            site_dir=site_dir,
            max_retries=max_retries,
            max_llm_cost_per_repo=max_llm_cost_per_repo,
            max_total_daily_llm_cost=max_total_daily_llm_cost,
            allow_runc=allow_runc,
            base_path=base_path,
            site_url=site_url,
        )
        if execute
        else []
    )

    report = _daily_report(
        now,
        provider.name,
        provider.errors,
        candidates,
        outcomes,
        execute,
        previous_report,
        operating_date,
    )
    atomic_json(report_path, report)
    build_site(
        reviews_root,
        site_dir,
        hunts_root=hunts_root,
        base_path=base_path,
        site_url=site_url,
    )
    return report


def _qualification_blocks_retry(candidate: dict[str, Any], now: datetime) -> bool:
    qualification = candidate.get("qualification") or {}
    if not (
        qualification.get("revision") == QUALIFICATION_REVISION
        and qualification.get("inspection_revision") == INSPECTION_REVISION
        and qualification.get("passed") is False
    ):
        return False
    return (
        not qualification.get("retryable_error")
        or str(qualification.get("retry_after") or "") > now.isoformat()
    )


def run_selected(
    *,
    manifest_path: Path,
    backlog_path: Path,
    work_root: Path,
    reviews_root: Path,
    site_dir: Path,
    max_retries: int = 2,
    max_llm_cost_per_repo: float = 5.0,
    max_total_daily_llm_cost: float = 25.0,
    execute: bool = False,
    missing_evaluator_credential: bool = False,
    allow_runc: bool = False,
    base_path: str = "/",
    site_url: str = "http://localhost:8000",
    now: datetime | None = None,
    run_date: date | None = None,
) -> dict[str, Any]:
    """Execute only the exact commit-keyed selection recorded by discovery."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    operating_date = run_date or now.date()
    if abs((operating_date - now.date()).days) > 1:
        raise ValueError("daily operating date must be today or an adjacent UTC date")
    if execute == missing_evaluator_credential:
        raise ValueError("selected execution requires exactly one evaluator availability mode")
    if execute and not allow_runc:
        raise ValueError("--execute requires --allow-runc to acknowledge the documented residual risk")
    if not 1 <= max_retries <= 10:
        raise ValueError("maximum retries must be between 1 and 10")
    if (
        not math.isfinite(max_llm_cost_per_repo)
        or not math.isfinite(max_total_daily_llm_cost)
        or max_llm_cost_per_repo <= 0
        or max_total_daily_llm_cost < max_llm_cost_per_repo
    ):
        raise ValueError("LLM cost limits must be positive and the daily limit must cover one repository")

    manifest, backlog, selected = _load_selected_manifest(manifest_path, backlog_path, operating_date)
    completed = {
        (str(outcome.get("repository")), str(outcome.get("commit_sha")))
        for outcome in manifest.get("outcomes", [])
        if isinstance(outcome, dict) and outcome.get("status") == "PUBLISHED"
    }
    pending = [
        candidate
        for candidate in selected
        if (str(candidate["repository"]), str(candidate["current_commit_sha"])) not in completed
        and not candidate.get("retry_exhausted")
    ]
    if missing_evaluator_credential:
        outcomes = []
        for candidate in pending:
            candidate["candidate_status"] = "BLOCKED_MISSING_EVALUATOR_CREDENTIAL"
            outcomes.append(
                {
                    "repository": candidate["repository"],
                    "commit_sha": candidate["current_commit_sha"],
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_EVALUATOR_CREDENTIAL",
                    "reason": "No evaluator credential was configured; candidate execution did not start.",
                    "llm_cost_usd": 0,
                }
            )
    else:
        outcomes = _execute_candidates(
            pending,
            backlog=backlog,
            backlog_path=backlog_path,
            previous_report=manifest,
            work_root=work_root,
            reviews_root=reviews_root,
            site_dir=site_dir,
            max_retries=max_retries,
            max_llm_cost_per_repo=max_llm_cost_per_repo,
            max_total_daily_llm_cost=max_total_daily_llm_cost,
            allow_runc=allow_runc,
            base_path=base_path,
            site_url=site_url,
        )

    candidates = backlog["candidates"]
    backlog.update({"updated_at": now.isoformat(), "candidates": candidates})
    atomic_json(backlog_path, backlog)
    report = _daily_report(
        now,
        str(manifest["provider"]),
        [],
        candidates,
        outcomes,
        execute,
        manifest,
        operating_date,
    )
    report["selected"] = [_candidate_summary(candidate) for candidate in selected]
    atomic_json(manifest_path, report)
    build_site(
        reviews_root,
        site_dir,
        hunts_root=manifest_path.parent,
        base_path=base_path,
        site_url=site_url,
    )
    return report


def _load_selected_manifest(
    manifest_path: Path, backlog_path: Path, operating_date: date
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    manifest = _safe_json(manifest_path)
    date_text = operating_date.isoformat()
    if manifest is None or manifest.get("schema_version") != 1:
        raise ValueError("unsupported selection manifest schema")
    if manifest.get("date") != date_text:
        raise ValueError("selection manifest date does not match the operating date")
    if not isinstance(manifest.get("provider"), str) or not isinstance(manifest.get("outcomes"), list):
        raise ValueError("selection manifest metadata is invalid")
    raw_selected = manifest.get("selected")
    counts = manifest.get("counts")
    if not isinstance(raw_selected, list) or len(raw_selected) > 5:
        raise ValueError("selection manifest must contain at most five candidates")
    if not isinstance(counts, dict) or counts.get("selected") != len(raw_selected):
        raise ValueError("selection manifest count does not match its candidates")

    backlog = _load_backlog(backlog_path)
    by_identity = {
        (
            str(candidate["repository"]),
            str(candidate["url"]),
            str(candidate["current_commit_sha"]),
        ): candidate
        for candidate in backlog["candidates"]
    }
    selected: list[dict[str, Any]] = []
    manifest_identities: list[tuple[str, str, str]] = []
    for item in raw_selected:
        if not isinstance(item, dict):
            raise ValueError("selection manifest candidates must be objects")
        repository = item.get("repository")
        url = item.get("url")
        commit = item.get("commit_sha")
        if not all(isinstance(value, str) for value in (repository, url, commit)):
            raise ValueError("selection manifest candidate identity is invalid")
        ref = parse_repo_url(str(url))
        if repository != ref.slug or url != ref.url or re.fullmatch(r"[0-9a-f]{40}", str(commit)) is None:
            raise ValueError("selection manifest candidate identity is not canonical")
        identity = (str(repository), str(url), str(commit))
        if identity in manifest_identities:
            raise ValueError("selection manifest contains a duplicate candidate")
        candidate = by_identity.get(identity)
        if candidate is None:
            raise ValueError("selection manifest candidate does not match the backlog")
        qualification = candidate.get("qualification")
        gates = qualification.get("gates") if isinstance(qualification, dict) else None
        if (
            candidate.get("metadata_qualified") is not True
            or not isinstance(qualification, dict)
            or qualification.get("revision") != QUALIFICATION_REVISION
            or qualification.get("inspection_revision") != INSPECTION_REVISION
            or qualification.get("passed") is not True
            or not isinstance(gates, dict)
            or not gates
            or any(not isinstance(gate, dict) or gate.get("passed") is not True for gate in gates.values())
        ):
            raise ValueError("selection manifest candidate lacks a current passed qualification")
        if _selection_date(candidate) != date_text:
            raise ValueError("selection manifest candidate was not selected for the operating date")
        manifest_identities.append(identity)
        selected.append(candidate)

    backlog_identities = [
        (
            str(candidate["repository"]),
            str(candidate["url"]),
            str(candidate["current_commit_sha"]),
        )
        for candidate in backlog["candidates"]
        if _selection_date(candidate) == date_text
    ]
    if len(backlog_identities) != len(manifest_identities) or set(backlog_identities) != set(
        manifest_identities
    ):
        raise ValueError("selection manifest does not match the backlog selection")
    return manifest, backlog, selected


def _execute_candidates(
    selected: list[dict[str, Any]],
    *,
    backlog: dict[str, Any],
    backlog_path: Path,
    previous_report: dict[str, Any] | None,
    work_root: Path,
    reviews_root: Path,
    site_dir: Path,
    max_retries: int,
    max_llm_cost_per_repo: float,
    max_total_daily_llm_cost: float,
    allow_runc: bool,
    base_path: str,
    site_url: str,
) -> list[dict[str, Any]]:
    prior_costs: dict[tuple[str, str], float] = {}
    for outcome in (previous_report or {}).get("outcomes", []):
        if not isinstance(outcome, dict):
            continue
        try:
            cost = float(outcome.get("llm_cost_usd") or 0)
        except (TypeError, ValueError):
            continue
        if math.isfinite(cost) and cost >= 0:
            prior_costs[(str(outcome.get("repository")), str(outcome.get("commit_sha")))] = cost
    outcomes: list[dict[str, Any]] = []
    for candidate in selected:
        if sum(prior_costs.values()) + max_llm_cost_per_repo > max_total_daily_llm_cost + 0.001:
            candidate["candidate_status"] = "BLOCKED_RESOURCE_LIMIT"
            outcomes.append(
                {
                    "repository": candidate["repository"],
                    "commit_sha": candidate["current_commit_sha"],
                    "status": "BLOCKED_RESOURCE_LIMIT",
                    "reason": "Daily LLM cost ceiling reserved insufficient budget for another repository.",
                    "llm_cost_usd": 0,
                }
            )
            atomic_json(backlog_path, backlog)
            continue
        candidate["candidate_status"] = "EVALUATING"
        candidate["evaluation_attempts"] = int(candidate.get("evaluation_attempts") or 0) + 1
        candidate["retry_exhausted"] = candidate["evaluation_attempts"] >= max_retries
        atomic_json(backlog_path, backlog)
        previous_repo_limit = os.environ.get("WORTHIT_MAX_LLM_COST_PER_REPO")
        os.environ["WORTHIT_MAX_LLM_COST_PER_REPO"] = str(max_llm_cost_per_repo)
        try:
            result = evaluate_repository(
                str(candidate["url"]),
                commit=str(candidate["current_commit_sha"]),
                work_root=work_root,
                reviews_root=reviews_root,
                site_dir=site_dir,
                allow_runc=allow_runc,
                base_path=base_path,
                site_url=site_url,
            )
            candidate["candidate_status"] = "PUBLISHED"
            outcomes.append(
                {
                    "repository": candidate["repository"],
                    "commit_sha": candidate["current_commit_sha"],
                    "status": "PUBLISHED",
                    "attempt": candidate["evaluation_attempts"],
                    "score": result["score"],
                    "confidence": result["confidence"],
                    "verdict": result["verdict"],
                    "llm_cost_usd": result["llm_cost_usd"],
                }
            )
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            ref = parse_repo_url(str(candidate["url"]))
            run_dir = work_root / "runs" / f"{ref.owner}--{ref.name}" / str(candidate["current_commit_sha"])
            status = _evaluation_failure_status(error, run_dir)
            candidate["candidate_status"] = status
            outcomes.append(
                {
                    "repository": candidate["repository"],
                    "commit_sha": candidate["current_commit_sha"],
                    "status": status,
                    "attempt": candidate["evaluation_attempts"],
                    "reason": redact(clean_text(str(error), 1_000)),
                    "llm_cost_usd": model_cost(run_dir),
                }
            )
        finally:
            if previous_repo_limit is None:
                os.environ.pop("WORTHIT_MAX_LLM_COST_PER_REPO", None)
            else:
                os.environ["WORTHIT_MAX_LLM_COST_PER_REPO"] = previous_repo_limit
        outcome = outcomes[-1]
        prior_costs[(str(outcome["repository"]), str(outcome["commit_sha"]))] = float(
            outcome.get("llm_cost_usd") or 0
        )
        atomic_json(backlog_path, backlog)
    return outcomes


def _daily_report(
    now: datetime,
    provider_name: str,
    new_provider_errors: list[str],
    candidates: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    execute: bool,
    previous: dict[str, Any] | None,
    run_date: date,
) -> dict[str, Any]:
    date_text = run_date.isoformat()
    selected = [candidate for candidate in candidates if _selection_date(candidate) == date_text]
    selected_keys = {str(candidate["repository"]).casefold() for candidate in selected}
    metadata_eligible = [candidate for candidate in candidates if candidate.get("metadata_qualified")]
    metadata_eligible_today = [
        candidate
        for candidate in metadata_eligible
        if any(
            str(event.get("discovered_at") or "")[:10] == date_text
            for event in candidate.get("discovery_events", [])
        )
    ]
    interesting_not_tested = [
        _candidate_summary(candidate)
        for candidate in metadata_eligible
        if str(candidate["repository"]).casefold() not in selected_keys
    ][:10]
    skipped = []
    for candidate in candidates:
        if candidate.get("candidate_status") in {"SELECTED", "EVALUATING", "PUBLISHED"}:
            continue
        reasons = [
            f"{name}: {gate['reason']}"
            for name, gate in (candidate.get("metadata_gates") or {}).items()
            if not gate.get("passed")
        ]
        qualification = candidate.get("qualification") or {}
        reasons.extend(
            f"{name}: {gate['reason']}"
            for name, gate in (qualification.get("gates") or {}).items()
            if not gate.get("passed")
        )
        if qualification.get("error"):
            reasons.append(str(qualification["error"]))
        if candidate.get("candidate_status") == "ALREADY_TESTED":
            reasons.append("Exact commit already has a published review.")
        if candidate.get("retry_exhausted"):
            reasons.append(
                f"Evaluation retry limit reached ({int(candidate.get('evaluation_attempts') or 0)} attempts)."
            )
        if candidate.get("candidate_status") == "BLOCKED_RESOURCE_LIMIT":
            reasons.append("Daily LLM cost ceiling left insufficient budget for execution.")
        if reasons:
            skipped.append({**_candidate_summary(candidate), "reasons": reasons})
    merged_outcomes = {
        (str(outcome.get("repository")), str(outcome.get("commit_sha"))): outcome
        for outcome in (previous or {}).get("outcomes", [])
        if isinstance(outcome, dict)
    }
    merged_outcomes.update(
        {(str(outcome["repository"]), str(outcome["commit_sha"])): outcome for outcome in outcomes}
    )
    all_outcomes = list(merged_outcomes.values())
    completed = [outcome for outcome in all_outcomes if outcome["status"] == "PUBLISHED"]
    best = max(completed, key=lambda item: int(item["score"])) if completed else None
    worst = min(completed, key=lambda item: int(item["score"])) if completed else None
    provider_errors = list(
        dict.fromkeys(
            [
                *(error for error in (previous or {}).get("provider_errors", []) if isinstance(error, str)),
                *new_provider_errors,
            ]
        )
    )
    return {
        "schema_version": 1,
        "date": date_text,
        "generated_at": now.isoformat(),
        "provider": provider_name,
        "provider_errors": provider_errors,
        "execution_enabled": execute or bool((previous or {}).get("execution_enabled")),
        "counts": {
            "discovered": sum(
                any(
                    str(event.get("discovered_at") or "")[:10] == date_text
                    for event in candidate.get("discovery_events", [])
                )
                for candidate in candidates
            ),
            "backlog": len(candidates),
            "metadata_qualified": len(metadata_eligible_today),
            "deep_inspected": sum(
                (candidate.get("qualification") or {}).get("revision") == QUALIFICATION_REVISION
                and (candidate.get("qualification") or {}).get("inspection_revision") == INSPECTION_REVISION
                and str((candidate.get("qualification") or {}).get("inspected_at") or "")[:10] == date_text
                for candidate in candidates
            ),
            "qualified": sum(
                (candidate.get("qualification") or {}).get("passed") is True
                and str((candidate.get("qualification") or {}).get("inspected_at") or "")[:10] == date_text
                for candidate in candidates
            ),
            "selected": len(selected),
            "tested": sum(
                outcome["status"]
                in {
                    "PUBLISHED",
                    "INSTALL_FAILED",
                    "TIMEOUT",
                    "HOLD_INSUFFICIENT_EVIDENCE",
                    "HOLD_EDITORIAL_REVIEW",
                    "FAILED",
                }
                for outcome in all_outcomes
            ),
            "completed": len(completed),
            "blocked_or_failed": len(all_outcomes) - len(completed),
        },
        "selected": [_candidate_summary(candidate) for candidate in selected],
        "outcomes": all_outcomes,
        "llm_cost_usd": round(sum(float(outcome.get("llm_cost_usd") or 0) for outcome in all_outcomes), 6),
        "best_tool": best,
        "biggest_disappointment": worst if worst and int(worst["score"]) < 60 else None,
        "interesting_not_tested": interesting_not_tested,
        "skipped": skipped[:50],
    }


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "repository": candidate["repository"],
        "url": candidate["url"],
        "commit_sha": candidate["current_commit_sha"],
        "description": candidate.get("description", ""),
        "category": candidate.get("category", "other"),
        "priority": candidate.get("priority", {}),
        "status": candidate.get("candidate_status", "DISCOVERED"),
    }


def _evaluation_failure_status(error: BaseException, run_dir: Path) -> str:
    if isinstance(error, TimeoutError | subprocess.TimeoutExpired):
        return "TIMEOUT"
    explicit = getattr(error, "outcome_status", None)
    if explicit in {"HOLD_INSUFFICIENT_EVIDENCE", "HOLD_EDITORIAL_REVIEW"}:
        return str(explicit)
    state = _safe_json(run_dir / "state.json") or {}
    failed = max(
        (
            (str(value.get("updated_at") or ""), name)
            for name, value in (state.get("stages") or {}).items()
            if isinstance(value, dict) and value.get("status") == "FAILED"
        ),
        default=("", ""),
    )[1]
    if failed == "risk_assess":
        return "HOLD_SECURITY_REVIEW"
    if failed == "review":
        fact = _safe_json(run_dir / "fact-check.json")
        return (
            "HOLD_INSUFFICIENT_EVIDENCE" if fact and fact.get("passed") is False else "HOLD_EDITORIAL_REVIEW"
        )
    if failed in {"diagnostic_run", "clean_replay"}:
        names = ("warm.json",) if failed == "diagnostic_run" else ("final.json", "replay.json")
        for name in names:
            execution = _safe_json(run_dir / name)
            if not execution:
                continue
            raw_tests = execution.get("tests")
            tests = raw_tests if isinstance(raw_tests, list) else []
            traces = [execution.get("provision"), execution.get("install"), *tests]
            if any(
                isinstance(trace, dict)
                and (trace.get("timed_out") is True or trace.get("failure_reason") == "TIMEOUT")
                for trace in traces
            ):
                return "TIMEOUT"
            if execution.get("status") == "INSTALL_FAILED":
                return "INSTALL_FAILED"
        return "BLOCKED"
    if failed in {"prepare", "evaluate"}:
        return "BLOCKED"
    return "FAILED"


def _selection_date(candidate: dict[str, Any]) -> str:
    return str(candidate.get("selection_date") or candidate.get("selected_at") or "")[:10]


def _safe_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def default_provider(work_root: Path) -> GitHubDiscoveryProvider:
    return GitHubDiscoveryProvider(work_root / "cache")
