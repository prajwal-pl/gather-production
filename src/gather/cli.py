"""gather CLI."""

from __future__ import annotations

import argparse
import os
import sys


def _storage_options() -> argparse.ArgumentParser:
    """``--data-dir`` works before or after the subcommand."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--data-dir",
        default=argparse.SUPPRESS,
        metavar="PATH",
        help="directory for the database, checkpoints and logs "
        "(default: the OS app-data directory)",
    )
    return parser


def main() -> int:
    storage = _storage_options()
    parser = argparse.ArgumentParser(
        prog="gather",
        description="meeting-to-task follow-up agent",
        parents=[storage],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run the end-to-end demo", parents=[storage])
    demo.add_argument("--live", action="store_true", help="use Groq/OpenRouter from .env")
    demo.add_argument("--keep", action="store_true", help="keep the demo database")
    demo.add_argument("--auto-approve", action="store_true", help="skip the approval gate")

    serve = sub.add_parser(
        "serve", help="run the webhook + approval API (development only)", parents=[storage]
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    check = sub.add_parser("check", help="probe each model in the chain", parents=[storage])
    check.add_argument("--timeout", type=int, default=45)

    sub.add_parser("seed", help="create a local org with demo members", parents=[storage])

    sub.add_parser("sync", help="poll connected providers once", parents=[storage])

    args = parser.parse_args()

    # Downstream settings loaders read the environment. One assignment keeps
    # `serve`, `seed` and the eventual engine on the same path.
    data_dir = getattr(args, "data_dir", None)
    if data_dir:
        os.environ["GATHER_DATA_DIR"] = data_dir

    if args.command == "demo":
        from gather.demo import main as run_demo

        argv: list[str] = []
        if args.live:
            argv.append("--live")
        if args.keep:
            argv.append("--keep")
        if args.auto_approve:
            argv.append("--auto-approve")
        if data_dir:
            argv.extend(["--data-dir", data_dir])
        return run_demo(argv)

    if args.command == "serve":
        try:
            import uvicorn

            from gather.api import create_app
        except ImportError:
            print(
                "headless mode is development-only; install it with "
                "`uv sync --group dev`",
                file=sys.stderr,
            )
            return 1

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

    if args.command == "sync":
        from gather.runtime import build_runtime

        runtime = build_runtime()
        try:
            outcomes = runtime.poll_once()
        finally:
            runtime.close()

        if not outcomes:
            print("nothing due")
            return 0

        failed = False
        for outcome in outcomes:
            if outcome.error:
                failed = True
                print(f"{outcome.provider}: error: {outcome.error}")
            else:
                print(
                    f"{outcome.provider}: {outcome.events} new, "
                    f"{outcome.duplicates} duplicate(s)"
                )
            print(f"  next sync: {outcome.next_sync_at or '-'}")
        return 1 if failed else 0

    if args.command == "check":
        from gather.check import main as run_check

        return run_check(["--timeout", str(args.timeout)])

    if args.command == "seed":
        from gather.seed import main as run_seed

        return run_seed()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
