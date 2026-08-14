from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from .discovery import default_provider, run_daily, run_selected
from .pipeline import evaluate_repository
from .site import build_site, verify_site


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="worthit", description="Execution-backed verification for public GitHub CLIs"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser(
        "evaluate", help="inspect, run, evaluate, review, and publish one repository locally"
    )
    evaluate.add_argument("repository_url")
    evaluate.add_argument(
        "--commit", help="exact Git commit; defaults to the repository's current default-branch head"
    )
    evaluate.add_argument(
        "--allow-runc",
        action="store_true",
        help="acknowledge the documented runc residual risk for TRUSTED repositories",
    )
    evaluate.add_argument("--work-root", type=Path, default=Path(".worthit"))
    evaluate.add_argument(
        "--run-dir", type=Path, help="resume an existing run directory for the resolved commit"
    )
    evaluate.add_argument("--reviews-root", type=Path, default=Path("reviews"))
    evaluate.add_argument("--site-dir", type=Path, default=Path("_site"))
    evaluate.add_argument("--base-path", default=os.environ.get("WORTHIT_BASE_PATH", "/"))
    evaluate.add_argument("--site-url", default=os.environ.get("WORTHIT_SITE_URL", "http://localhost:8000"))

    site = commands.add_parser("build-site", help="rebuild the static site from validated review artifacts")
    site.add_argument("--reviews-root", type=Path, default=Path("reviews"))
    site.add_argument("--hunts-root", type=Path, default=Path("hunts"))
    site.add_argument("--site-dir", type=Path, default=Path("_site"))
    site.add_argument("--base-path", default=os.environ.get("WORTHIT_BASE_PATH", "/"))
    site.add_argument("--site-url", default=os.environ.get("WORTHIT_SITE_URL", "http://localhost:8000"))

    verify = commands.add_parser("verify-site", help="check a built site for unsafe files and broken links")
    verify.add_argument("--site-dir", type=Path, default=Path("_site"))
    verify.add_argument("--base-path", default=os.environ.get("WORTHIT_BASE_PATH", "/"))

    daily = commands.add_parser(
        "daily", help="discover, qualify, select up to five, and optionally evaluate candidates"
    )
    daily.add_argument("--backlog", type=Path, default=Path("data/candidates.json"))
    daily.add_argument("--hunts-root", type=Path, default=Path("hunts"))
    daily.add_argument("--work-root", type=Path, default=Path(".worthit"))
    daily.add_argument("--reviews-root", type=Path, default=Path("reviews"))
    daily.add_argument("--site-dir", type=Path, default=Path("_site"))
    daily.add_argument("--discovery-limit", type=int, default=os.environ.get("WORTHIT_DISCOVERY_LIMIT", "80"))
    daily.add_argument("--qualify-limit", type=int, default=os.environ.get("WORTHIT_QUALIFY_LIMIT", "10"))
    daily.add_argument("--daily-limit", type=int, default=os.environ.get("DAILY_TEST_LIMIT", "5"))
    daily.add_argument("--max-retries", type=int, default=os.environ.get("WORTHIT_MAX_RETRIES", "2"))
    daily.add_argument(
        "--max-llm-cost-per-repo",
        type=float,
        default=os.environ.get("MAX_LLM_COST_PER_REPO", "5"),
    )
    daily.add_argument(
        "--max-total-daily-llm-cost",
        type=float,
        default=os.environ.get("MAX_TOTAL_DAILY_LLM_COST", "25"),
    )
    daily.add_argument(
        "--execute",
        action="store_true",
        help="run selected candidates; without this flag the command stops after selection",
    )
    daily.add_argument(
        "--selection-manifest",
        type=Path,
        help="consume an existing daily hunt selection without running discovery again",
    )
    daily.add_argument(
        "--missing-evaluator-credential",
        action="store_true",
        help="record selected candidates as blocked without starting evaluation",
    )
    daily.add_argument(
        "--allow-runc",
        action="store_true",
        help="acknowledge the documented runc residual risk when --execute is used",
    )
    daily.add_argument("--base-path", default=os.environ.get("WORTHIT_BASE_PATH", "/"))
    daily.add_argument("--site-url", default=os.environ.get("WORTHIT_SITE_URL", "http://localhost:8000"))
    daily.add_argument(
        "--run-date",
        type=date.fromisoformat,
        help="UTC operating date shared across resumed daily jobs (YYYY-MM-DD)",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "evaluate":
            result = evaluate_repository(
                args.repository_url,
                commit=args.commit,
                work_root=args.work_root,
                reviews_root=args.reviews_root,
                site_dir=args.site_dir,
                allow_runc=args.allow_runc,
                base_path=args.base_path,
                site_url=args.site_url,
                run_dir_override=args.run_dir,
            )
            print(json.dumps(result, indent=2))
        elif args.command == "build-site":
            count = build_site(
                args.reviews_root,
                args.site_dir,
                hunts_root=args.hunts_root,
                base_path=args.base_path,
                site_url=args.site_url,
            )
            print(f"built {args.site_dir} from {count} published review(s)")
        elif args.command == "verify-site":
            print(json.dumps(verify_site(args.site_dir, base_path=args.base_path), indent=2))
        else:
            if args.selection_manifest is not None:
                if args.run_date is None:
                    raise ValueError("--selection-manifest requires --run-date")
                report = run_selected(
                    manifest_path=args.selection_manifest,
                    backlog_path=args.backlog,
                    work_root=args.work_root,
                    reviews_root=args.reviews_root,
                    site_dir=args.site_dir,
                    max_retries=args.max_retries,
                    max_llm_cost_per_repo=args.max_llm_cost_per_repo,
                    max_total_daily_llm_cost=args.max_total_daily_llm_cost,
                    execute=args.execute,
                    missing_evaluator_credential=args.missing_evaluator_credential,
                    allow_runc=args.allow_runc,
                    base_path=args.base_path,
                    site_url=args.site_url,
                    run_date=args.run_date,
                )
            else:
                if args.missing_evaluator_credential:
                    raise ValueError("--missing-evaluator-credential requires --selection-manifest")
                report = run_daily(
                    provider=default_provider(args.work_root),
                    backlog_path=args.backlog,
                    hunts_root=args.hunts_root,
                    work_root=args.work_root,
                    reviews_root=args.reviews_root,
                    site_dir=args.site_dir,
                    discovery_limit=args.discovery_limit,
                    qualify_limit=args.qualify_limit,
                    daily_limit=args.daily_limit,
                    max_retries=args.max_retries,
                    max_llm_cost_per_repo=args.max_llm_cost_per_repo,
                    max_total_daily_llm_cost=args.max_total_daily_llm_cost,
                    execute=args.execute,
                    allow_runc=args.allow_runc,
                    base_path=args.base_path,
                    site_url=args.site_url,
                    run_date=args.run_date,
                )
            print(
                json.dumps(
                    {
                        "date": report["date"],
                        "counts": report["counts"],
                        "selected": [item["repository"] for item in report["selected"]],
                        "hunt": str(args.hunts_root / f"{report['date']}.json"),
                    },
                    indent=2,
                )
            )
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"worthit: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
