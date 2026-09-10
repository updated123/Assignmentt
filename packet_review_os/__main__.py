"""Command line entry point.

Four commands, in the order an operator meets them:

    python -m packet_review_os check     # is my configuration valid?
    python -m packet_review_os serve     # start the web app for the reviewer
    python -m packet_review_os review X  # score one packet from a file
    python -m packet_review_os eval      # run the evaluation set

Every command turns a configuration problem into a readable message and exit
code 2, rather than a traceback.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import ROOT, ConfigError, list_roles, settings, validate_all
from .pdf_extract import PdfExtractError, extract_pdf_text
from .pipeline import run_review, safe_log_packet
from .schemas import PacketInput
from .storage import init_db, save_review

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_CONFIG = 2

logger = logging.getLogger("packet_review_os.cli")


def _force_utf8_console() -> None:
    try:
        # Windows consoles default to cp1252 and would mangle quoted packet text.
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError) as exc:
        print(f"Note: could not switch console encoding to UTF-8 ({exc}).", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="packet-review-os",
        description="Packet Review OS - scorecard-grounded candidate packet reviews.",
    )
    parser.add_argument("--log-level", default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Start the web app for a non-developer reviewer")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    review = sub.add_parser("review", help="Review a packet file from the CLI")
    review.add_argument("path", help="Path to a .txt packet or .pdf resume")
    review.add_argument("--role", default="fullstack_engineer")
    review.add_argument("--notes", default="", help="Recruiter notes to append")
    review.add_argument("--name", default="")
    review.add_argument("--job-url", default="")
    review.add_argument("--json", action="store_true", help="Print the full structured result")

    ev = sub.add_parser("eval", help="Run the evaluation set")
    ev.add_argument("--skip-llm-path", action="store_true")
    ev.add_argument("--verbose", action="store_true")
    ev.add_argument("--quiet", action="store_true")

    sub.add_parser("roles", help="List configured role scorecards")
    sub.add_parser("check", help="Validate configuration and report problems")
    return parser


def _cmd_check() -> int:
    problems = validate_all()
    if not problems:
        roles = list_roles()
        print(f"Configuration OK. {len(roles)} role scorecard(s) loaded:")
        for role in roles:
            print(f"  - {role['id']}: {role['title']}")
        cfg = settings()
        print(f"Model: {'enabled (' + cfg.openai_model + ')' if cfg.llm_enabled else 'not configured (extractive only)'}")
        print(f"Database: {cfg.database_path}")
        if cfg.binds_publicly and not cfg.api_token:
            print(
                f"WARNING: APP_HOST is {cfg.app_host} (not loopback) and API_TOKEN is empty. "
                "Anyone who can reach this port can read stored reviews.",
                file=sys.stderr,
            )
        return EXIT_OK
    print(f"Found {len(problems)} configuration problem(s):", file=sys.stderr)
    for problem in problems:
        print(f"\n{problem}", file=sys.stderr)
    return EXIT_CONFIG


def _cmd_serve(args) -> int:
    import uvicorn

    cfg = settings()
    host = args.host or cfg.app_host
    port = args.port or cfg.app_port
    problems = validate_all()
    if problems:
        print("Refusing to start: configuration is invalid.", file=sys.stderr)
        for problem in problems:
            print(f"\n{problem}", file=sys.stderr)
        return EXIT_CONFIG
    if host not in {"127.0.0.1", "localhost", "::1"} and not cfg.api_token:
        print(
            f"\n*** WARNING: binding to {host}, which is reachable from other machines, "
            "with no API_TOKEN set.\n"
            "*** Candidate reviews would be readable by anyone who can reach this port.\n"
            "*** Set API_TOKEN in .env, or bind to 127.0.0.1.\n",
            file=sys.stderr,
        )
    print(f"Packet Review OS on http://{host}:{port}  (Ctrl+C to stop)")
    uvicorn.run("packet_review_os.web:app", host=host, port=port, reload=False)
    return EXIT_OK


def _cmd_review(args) -> int:
    path = Path(args.path)
    if not path.exists():
        path = ROOT / args.path
    if not path.exists():
        print(f"File not found: {args.path}", file=sys.stderr)
        return EXIT_RUNTIME

    pdf_text = ""
    packet_text = ""
    if path.suffix.lower() == ".pdf":
        try:
            pdf_text = extract_pdf_text(path.read_bytes(), path.name)
        except PdfExtractError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_RUNTIME
    else:
        try:
            packet_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(
                f"{path.name} is not UTF-8 text. Re-save it as UTF-8, or pass a PDF.",
                file=sys.stderr,
            )
            return EXIT_RUNTIME

    result = run_review(
        PacketInput(
            role_id=args.role,
            packet_text=packet_text,
            recruiter_notes=args.notes,
            candidate_name=args.name,
            job_url=args.job_url,
            source="cli",
        ),
        pdf_text=pdf_text,
    )
    init_db()
    save_review(result, safe_log_packet(packet_text or pdf_text))

    if args.json:
        print(result.model_dump_json(indent=2))
        return EXIT_OK

    print(f"{result.candidate_name} -> {result.next_action_label}")
    print(f"Confidence {result.confidence_label} ({result.confidence})  overall {result.overall_score}")
    print(result.summary)
    print(f"Why: {result.why_this_action}")
    print(f"Run id {result.run_id}  {result.engine_mode.value}  {result.latency_ms}ms")
    if result.draft_email:
        print(f"Draft prepared ({result.draft_email.purpose}) - not sent.")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
    if result.exceptions:
        print("Exceptions:")
        for item in result.exceptions:
            print(f"  - {item}")
    return EXIT_OK


def _cmd_eval(args) -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from evaluation.run_eval import main as eval_main

    forwarded = []
    if args.skip_llm_path:
        forwarded.append("--skip-llm-path")
    if args.verbose:
        forwarded.append("--verbose")
    if args.quiet:
        forwarded.append("--quiet")
    return eval_main(forwarded)


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, (args.log_level or settings().log_level).upper(), logging.INFO),
        format="%(levelname)s %(message)s",
    )

    try:
        if args.cmd == "check":
            return _cmd_check()
        if args.cmd == "serve":
            return _cmd_serve(args)
        if args.cmd == "roles":
            for role in list_roles():
                suffix = f"  [{role['error'].splitlines()[0]}]" if role.get("error") else ""
                print(f"{role['id']:24} {role['title']} ({role['company']}){suffix}")
            return EXIT_OK
        if args.cmd == "eval":
            return _cmd_eval(args)
        if args.cmd == "review":
            return _cmd_review(args)
    except ConfigError as exc:
        print(f"\nConfiguration problem:\n{exc}\n", file=sys.stderr)
        print("Run `python -m packet_review_os check` for the full report.", file=sys.stderr)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return EXIT_RUNTIME
    return EXIT_RUNTIME


if __name__ == "__main__":
    raise SystemExit(main())
