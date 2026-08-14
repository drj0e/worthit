from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .pipeline import evaluate_repository
from .site import build_site


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
    site.add_argument("--site-dir", type=Path, default=Path("_site"))
    site.add_argument("--base-path", default=os.environ.get("WORTHIT_BASE_PATH", "/"))
    site.add_argument("--site-url", default=os.environ.get("WORTHIT_SITE_URL", "http://localhost:8000"))

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
        else:
            count = build_site(
                args.reviews_root, args.site_dir, base_path=args.base_path, site_url=args.site_url
            )
            print(f"built {args.site_dir} from {count} published review(s)")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"worthit: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
