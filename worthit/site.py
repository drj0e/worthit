from __future__ import annotations

import html
import json
import math
import shutil
import urllib.parse
from pathlib import Path
from typing import Any

CSS = """*{box-sizing:border-box}body{margin:0;background:#f4f1e8;color:#171915;font:16px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}a{color:#174f3a}header,main,footer{max-width:1100px;margin:auto;padding:1rem 1.25rem}header{display:flex;gap:1rem;align-items:center;border-bottom:2px solid #171915}header nav{margin-left:auto;display:flex;gap:.9rem;flex-wrap:wrap}.brand{font-weight:900;text-decoration:none}.hero{padding:4rem 0 2rem;border-bottom:1px solid #777}.hero h1{font:800 clamp(2.4rem,8vw,6.5rem)/.95 Georgia,serif;max-width:900px;margin:.25rem 0}.kicker{text-transform:uppercase;letter-spacing:.12em}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}.card,.panel{background:#fff;border:1px solid #171915;padding:1rem;box-shadow:4px 4px 0 #171915}.score{font:800 2.5rem/1 Georgia,serif}.meta{color:#4e554d;font-size:.9rem}.badge{display:inline-block;border:1px solid;padding:.1rem .4rem;margin-right:.35rem}table{width:100%;border-collapse:collapse;background:#fff}th,td{text-align:left;vertical-align:top;border:1px solid #777;padding:.55rem;overflow-wrap:anywhere}h1,h2,h3{font-family:Georgia,serif}blockquote{margin-left:0;border-left:4px solid #174f3a;padding-left:1rem}code{overflow-wrap:anywhere}.pass{color:#12633f}.partial{color:#7a4f00}.fail{color:#9b1b1b}footer{margin-top:3rem;border-top:1px solid #777}.stack>*+*{margin-top:1.2rem}@media(max-width:700px){header{align-items:flex-start;flex-direction:column}header nav{margin:0}.hero{padding-top:2rem}table{font-size:.85rem}}"""


def build_site(
    reviews_root: Path, site_dir: Path, *, base_path: str = "/", site_url: str = "http://localhost:8000"
) -> int:
    base_path = _base_path(base_path)
    site_url = site_url.rstrip("/")
    reviews = []
    for path in reviews_root.rglob("review.json") if reviews_root.exists() else []:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not _valid_review(value):
            raise ValueError(f"invalid review artifact: {path}")
        value["_artifact_dir"] = str(path.parent.resolve())
        value["_site_path"] = (
            f"reviews/{_quote(value['repository'].split('/')[0])}/{_quote(value['repository'].split('/')[1])}/{value['commit_sha']}/"
        )
        reviews.append(value)
    reviews.sort(key=lambda item: item["tested_at"], reverse=True)
    _prepare_site_dir(site_dir)
    (site_dir / "assets").mkdir(parents=True)
    (site_dir / "assets" / "style.css").write_text(CSS, encoding="utf-8")

    nav = [
        ("Latest Reviews", "latest/"),
        ("Leaderboards", "leaderboards/"),
        ("Methodology", "methodology/"),
        ("How We Score", "how-we-score/"),
        ("How We Test", "how-we-test/"),
        ("About", "about/"),
    ]
    cards = (
        "".join(_card(review, base_path) for review in reviews[:12])
        or '<p class="panel">No reviews have passed publication yet.</p>'
    )
    home = f"""<section class="hero"><p class="kicker">Independent software testing lab</p><h1>AI tools tested so you don't have to.</h1><p>WorthIt inspects the claims, runs the code in a disposable environment, and publishes the evidence. We actually ran the thing.</p></section><section><h2>Latest verification reviews</h2><div class="grid">{cards}</div></section>"""
    _write_page(site_dir / "index.html", "WorthIt", home, nav, base_path)
    _write_page(
        site_dir / "latest" / "index.html",
        "Latest Reviews",
        f'<h1>Latest Reviews</h1><div class="grid">{cards}</div>',
        nav,
        base_path,
    )

    rows = "".join(
        f'<tr><td><a href="{html.escape(base_path + review["_site_path"], quote=True)}">{html.escape(review["repository"])}</a></td><td>{html.escape(str(review["score"]["overall"]))}</td><td>{html.escape(review["score"]["confidence"])}</td><td>{html.escape(review["score"]["verdict"])}</td><td><code>{html.escape(review["commit_sha"][:12])}</code></td><td>{html.escape(review["tested_at"][:10])}</td></tr>'
        for review in sorted(
            reviews, key=lambda item: (item["score"]["overall"], item["tested_at"]), reverse=True
        )
    )
    leaderboard = f"<h1>Leaderboards</h1><p>Scores stay attached to the exact tested commit and date.</p><table><thead><tr><th>Project</th><th>Score</th><th>Confidence</th><th>Verdict</th><th>Commit</th><th>Tested</th></tr></thead><tbody>{rows}</tbody></table>"
    _write_page(site_dir / "leaderboards" / "index.html", "Leaderboards", leaderboard, nav, base_path)

    static_pages = {
        "methodology": (
            "Methodology",
            '<h1>Methodology</h1><div class="panel stack"><p>WorthIt turns documented claims into falsifiable tests, critiques the plan, screens the repository, installs the exact commit, and runs user workflows plus failure cases.</p><p>A diagnostic run can repair an incorrect test oracle. Only a separately criticized contract that repeats in fresh disposable environments supports publication.</p><p>Unknown and blocked behavior stays UNVERIFIED. It never becomes a fabricated pass or an automatic zero.</p></div>',
        ),
        "how-we-score": (
            "How We Score",
            '<h1>How We Score</h1><div class="panel"><p>The composite is deterministic: 30% claim verification, 20% utility, 15% setup, 10% reliability, 10% performance and efficiency, 5% documentation, 5% safety and privacy posture, and 5% novelty.</p><p>Each dimension and reason remains visible. Confidence is separate from score. The Bullshit Ratio counts only tested claim weight contradicted by evidence; untested claim portions are excluded.</p></div>',
        ),
        "how-we-test": (
            "How We Test Safely",
            '<h1>How We Test Safely</h1><div class="panel stack"><p>Candidate code runs without host mounts, credentials, a Docker socket, or network. The root filesystem is read-only; CPU, RAM, processes, workspace, output, and time are bounded.</p><p>The current V1 backend uses rootful runc with seccomp and AppArmor for reviewed, established repositories only. It shares the host kernel and is not a VM-grade boundary. WorthIt records that residual risk rather than calling Docker perfectly safe.</p><p>Only redacted text evidence and validated JSON are published. Candidate executables and generated HTML are never copied into this site.</p></div>',
        ),
        "about": (
            "About",
            '<h1>About WorthIt</h1><div class="panel"><p>WorthIt is an autonomous verification lab for open-source AI and developer tools. Its core promise is simple: we actually ran the thing.</p><p>Discovery and prose support the work. Execution evidence is the product.</p></div>',
        ),
    }
    for slug, (title, body) in static_pages.items():
        _write_page(site_dir / slug / "index.html", title, body, nav, base_path)

    for review in reviews:
        artifact = Path(review["_artifact_dir"])
        relative = Path(review["_site_path"])
        destination = site_dir / relative
        destination.mkdir(parents=True, exist_ok=True)
        _copy_public_artifacts(artifact, destination)
        _write_page(
            destination / "index.html", f"{review['project']} review", _review_page(review), nav, base_path
        )

    _write_feed(site_dir / "feed.xml", reviews, site_url, base_path)
    _write_sitemap(site_dir / "sitemap.xml", reviews, site_url, base_path)
    (site_dir / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nSitemap: " + site_url + base_path + "sitemap.xml\n", encoding="utf-8"
    )
    return len(reviews)


def _review_page(review: dict[str, Any]) -> str:
    e = html.escape
    score = review["score"]
    bullshit_ratio = "N/A" if score["bullshit_ratio"] is None else f"{score['bullshit_ratio']}%"
    peak_ram = review["performance"]["peak_ram_bytes"]
    peak_ram_text = (
        "below the 200 ms sampling resolution" if peak_ram is None else f"{peak_ram / 1024 / 1024:.1f} MiB"
    )
    claims = "".join(
        f'<article class="panel"><h3>{e(claim["claim_id"])}: <span class="{e(claim["status"].casefold(), quote=True)}">{e(claim["status"])}</span></h3><blockquote>{e(claim["claim"])}</blockquote><p>{e(claim["explanation"])}</p><p class="meta">Tests: {e(", ".join(claim["test_ids"]) or "installation stage / no direct test")}</p></article>'
        for claim in review["claim_matrix"]
    )
    tests = "".join(
        f'<tr><td>{e(test["test_id"])}</td><td class="{e(test["status"].casefold(), quote=True)}">{e(test["status"])}</td><td>{e(str(test["exit_code"]))}</td><td>{e(str(test["duration_ms"]))} ms</td><td><a href="evidence/final/{_quote(test["test_id"])}/stdout.txt">stdout</a> · <a href="evidence/final/{_quote(test["test_id"])}/stderr.txt">stderr</a></td><td>{e(test["purpose"])}</td></tr>'
        for test in review["tests"]
    )
    dimensions = "".join(
        f"<tr><td>{e(name.replace('_', ' ').title())}</td><td>{e(str(value))}/100</td><td>{e(score['reasons'][name])}</td></tr>"
        for name, value in score["dimensions"].items()
    )

    def bullets(values: list[object]) -> str:
        return "<ul>" + "".join(f"<li>{e(str(item))}</li>" for item in values) + "</ul>"

    return f"""<p class="kicker">Execution-backed review</p><h1>{e(review["project"])}</h1><p class="meta"><a href="{e(review["repository_url"], quote=True)}">{e(review["repository"])}</a> · commit <code>{e(review["commit_sha"])}</code> · tool version <a href="evidence/final/version/stdout.txt"><code>{e(str(review.get("version") or "UNVERIFIED"))}</code></a> · tested {e(review["tested_at"][:10])}</p><div class="panel"><span class="score">{e(str(score["overall"]))}/100</span><p><span class="badge">{e(score["verdict"])}</span><span class="badge">{e(score["confidence"])} confidence</span><span class="badge">Bullshit Ratio {e(bullshit_ratio)}</span></p><p>{e(review["summary"])}</p></div><h2>Claim matrix</h2><div class="stack">{claims}</div><h2>What we ran</h2><table><thead><tr><th>Test</th><th>Result</th><th>Exit</th><th>Runtime</th><th>Evidence</th><th>Purpose</th></tr></thead><tbody>{tests}</tbody></table><h2>Setup and measurements</h2><div class="panel"><p>Setup: {e(review["setup"]["friction"])}. Install {review["setup"]["install_duration_ms"] / 1000:.2f}s; replay {review["setup"]["replay_install_duration_ms"] / 1000:.2f}s; interventions {e(str(review["setup"]["manual_interventions"]))}; candidate network {e(str(review["setup"]["candidate_network"]))}.</p><p>Slowest test {e(str(review["performance"]["slowest_test_ms"]))} ms; peak measured test RAM {e(peak_ram_text)}. No comparative baseline.</p><p>{e(review["setup"]["documented_vs_actual"])}</p><p>{e(review["setup"]["version_note"])}</p></div><h2>What broke</h2>{bullets(review["what_broke"])}<h2>Scorecard</h2><table><thead><tr><th>Dimension</th><th>Score</th><th>Reason</th></tr></thead><tbody>{dimensions}</tbody></table><h2>Who should use it</h2>{bullets(review["who_should_use_it"])}<h2>Who should skip it</h2>{bullets(review["who_should_skip_it"])}<h2>Limitations</h2>{bullets(review["limitations"])}<h2>Reproduction</h2><div class="panel"><p>Source SHA-256 <code>{e(str(review["reproduction"]["source_archive_sha256"]))}</code>. Candidate network: none. Clean replay matched: {e(str(review["reproduction"]["reproduced"]))}. Backend: {e(review["reproduction"]["runtime"])}</p><p><a href="risk.json">Risk assessment</a> · <a href="environment.json">Sandbox environment</a> · <a href="dependency-fetch.json">Dependency provenance</a> · <a href="checkout.json">Source acquisition</a> · <a href="reproducibility.json">Reproducibility</a></p></div>"""


def _card(review: dict[str, Any], base_path: str) -> str:
    path = html.escape(base_path + review["_site_path"], quote=True)
    return f'<article class="card"><p class="kicker">{html.escape(review["category"])}</p><h3><a href="{path}">{html.escape(review["project"])}</a></h3><div class="score">{html.escape(str(review["score"]["overall"]))}</div><p>{html.escape(review["summary"])}</p><p class="meta">{html.escape(review["score"]["verdict"])} · {html.escape(review["score"]["confidence"])} confidence · {html.escape(review["tested_at"][:10])}</p></article>'


def _write_page(path: Path, title: str, body: str, nav: list[tuple[str, str]], base_path: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    navigation = "".join(
        f'<a href="{html.escape(base_path + href, quote=True)}">{html.escape(label)}</a>'
        for label, href in nav
    )
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self'; img-src 'none'; base-uri 'none'; form-action 'none'"><title>{html.escape(title)} · WorthIt</title><meta name="description" content="Execution-backed reviews of open-source AI and developer tools"><link rel="stylesheet" href="{html.escape(base_path + "assets/style.css", quote=True)}"><link rel="alternate" type="application/atom+xml" href="{html.escape(base_path + "feed.xml", quote=True)}"></head><body><header><a class="brand" href="{html.escape(base_path, quote=True)}">WORTHIT / LAB NOTES</a><nav>{navigation}</nav></header><main>{body}</main><footer>Claims are hypotheses. Execution evidence comes first.</footer></body></html>"""
    path.write_text(document, encoding="utf-8")


def _copy_public_artifacts(source: Path, destination: Path) -> None:
    for name in (
        "review.json",
        "review.md",
        "score.json",
        "claims.json",
        "test-plan.json",
        "run.json",
        "evidence-manifest.json",
        "risk.json",
        "environment.json",
        "dependency-fetch.json",
        "checkout.json",
        "reproducibility.json",
        "fact-check.json",
        "editorial-critique.json",
    ):
        path = source / name
        if path.is_file() and not path.is_symlink():
            shutil.copyfile(path, destination / name)
    evidence = source / "evidence"
    if evidence.exists():
        for path in evidence.rglob("*.txt"):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 262_144:
                raise ValueError(f"unsafe site evidence artifact: {path}")
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def _write_feed(path: Path, reviews: list[dict[str, Any]], site_url: str, base_path: str) -> None:
    updated = html.escape(reviews[0]["tested_at"] if reviews else "1970-01-01T00:00:00+00:00")
    entries = "".join(
        f'<entry><title>{html.escape(review["project"])}</title><id>{html.escape(site_url + base_path + review["_site_path"])}</id><link href="{html.escape(site_url + base_path + review["_site_path"], quote=True)}"/><updated>{html.escape(review["tested_at"])}</updated><summary>{html.escape(review["summary"])}</summary></entry>'
        for review in reviews[:20]
    )
    path.write_text(
        f'<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>WorthIt Reviews</title><id>{html.escape(site_url + base_path)}</id><updated>{updated}</updated>{entries}</feed>',
        encoding="utf-8",
    )


def _write_sitemap(path: Path, reviews: list[dict[str, Any]], site_url: str, base_path: str) -> None:
    pages = ["", "latest/", "leaderboards/", "methodology/", "how-we-score/", "how-we-test/", "about/"]
    urls = [site_url + base_path + page for page in pages] + [
        site_url + base_path + review["_site_path"] for review in reviews
    ]
    body = "".join(f"<url><loc>{html.escape(url)}</loc></url>" for url in urls)
    path.write_text(
        f'<?xml version="1.0" encoding="utf-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
        encoding="utf-8",
    )


def _prepare_site_dir(path: Path) -> None:
    resolved = path.resolve()
    if (
        resolved in {Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve()}
        or len(resolved.parts) < 3
    ):
        raise ValueError("refusing to replace a broad site path")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def _valid_review(review: dict[str, Any]) -> bool:
    required = {
        "project",
        "repository",
        "repository_url",
        "commit_sha",
        "tested_at",
        "score",
        "claim_matrix",
        "tests",
    }
    if not required <= set(review):
        return False
    strings = ("project", "repository", "repository_url", "commit_sha", "tested_at", "category", "summary")
    if any(not isinstance(review.get(name), str) for name in strings):
        return False
    if (
        review["repository"].count("/") != 1
        or review["repository_url"] != f"https://github.com/{review['repository']}"
        or len(review["commit_sha"]) != 40
        or any(character not in "0123456789abcdef" for character in review["commit_sha"].casefold())
    ):
        return False
    score = review.get("score")
    setup = review.get("setup")
    performance = review.get("performance")
    reproduction = review.get("reproduction")
    if (
        not isinstance(score, dict)
        or not isinstance(setup, dict)
        or not isinstance(performance, dict)
        or not isinstance(reproduction, dict)
    ):
        return False
    if not (
        _number(score.get("overall"))
        and "bullshit_ratio" in score
        and (score.get("bullshit_ratio") is None or _number(score.get("bullshit_ratio")))
        and isinstance(score.get("verdict"), str)
        and isinstance(score.get("confidence"), str)
        and isinstance(score.get("dimensions"), dict)
        and isinstance(score.get("reasons"), dict)
        and all(
            isinstance(name, str) and _number(value) and isinstance(score["reasons"].get(name), str)
            for name, value in score["dimensions"].items()
        )
    ):
        return False
    if not (
        isinstance(setup.get("friction"), str)
        and _number(setup.get("install_duration_ms"))
        and _number(setup.get("replay_install_duration_ms"))
        and _number(setup.get("manual_interventions"))
        and isinstance(setup.get("candidate_network"), str)
        and isinstance(setup.get("documented_vs_actual"), str)
        and isinstance(setup.get("version_note"), str)
        and _number(performance.get("slowest_test_ms"))
        and (performance.get("peak_ram_bytes") is None or _number(performance.get("peak_ram_bytes")))
        and isinstance(reproduction.get("source_archive_sha256"), str)
        and isinstance(reproduction.get("reproduced"), bool)
        and isinstance(reproduction.get("runtime"), str)
    ):
        return False
    statuses = {"PASS", "PARTIAL", "FAIL", "BLOCKED", "UNVERIFIED", "NOT_APPLICABLE"}
    claims = review.get("claim_matrix")
    tests = review.get("tests")
    if not isinstance(claims, list) or not isinstance(tests, list):
        return False
    if any(
        not isinstance(claim, dict)
        or any(
            not isinstance(claim.get(name), str) for name in ("claim_id", "status", "claim", "explanation")
        )
        or claim.get("status") not in statuses
        or not isinstance(claim.get("test_ids"), list)
        or any(not isinstance(test_id, str) for test_id in claim["test_ids"])
        for claim in claims
    ):
        return False
    if any(
        not isinstance(test, dict)
        or any(not isinstance(test.get(name), str) for name in ("test_id", "status", "purpose"))
        or test.get("status") not in statuses
        or not (test.get("exit_code") is None or isinstance(test.get("exit_code"), int))
        or not _number(test.get("duration_ms"))
        for test in tests
    ):
        return False
    return all(
        isinstance(review.get(name), list)
        for name in ("what_broke", "who_should_use_it", "who_should_skip_it", "limitations")
    )


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _base_path(value: str) -> str:
    if not value.startswith("/") or ".." in value or "//" in value:
        raise ValueError("base path must be an absolute URL path without traversal")
    return value.rstrip("/") + "/"


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="-._~")
