"""gather CLI."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="gather", description="meeting-to-task follow-up agent")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run the end-to-end demo")
    demo.add_argument("--live", action="store_true", help="use Groq/OpenRouter from .env")
    demo.add_argument("--keep", action="store_true", help="keep the demo database")
    demo.add_argument("--auto-approve", action="store_true", help="skip the approval gate")

    serve = sub.add_parser("serve", help="run the webhook + approval API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    check = sub.add_parser("check", help="probe each model in the chain")
    check.add_argument("--timeout", type=int, default=45)

    sub.add_parser("seed", help="create a local org with demo members")

    args = parser.parse_args()

    if args.command == "demo":
        from gather.demo import main as run_demo

        argv: list[str] = []
        if args.live:
            argv.append("--live")
        if args.keep:
            argv.append("--keep")
        if args.auto_approve:
            argv.append("--auto-approve")
        return run_demo(argv)

    if args.command == "serve":
        import uvicorn

        from gather.api import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

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
