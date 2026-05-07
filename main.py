"""Spin Control orchestrator: ingest -> score -> draft -> digest."""

from __future__ import annotations

import argparse
import logging
import sys

import db
import digest as digest_module
import draft
import ingest
import score


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run_all(skip_send: bool = False) -> None:
    log = logging.getLogger("spin_control.main")
    db.init_db()

    log.info("=== Stage 1: ingest ===")
    new_count = ingest.ingest()
    log.info("Ingest produced %d new articles", new_count)

    log.info("=== Stage 2: score ===")
    scored_count = score.score_articles()
    log.info("Scoring produced %d scored articles", scored_count)

    log.info("=== Stage 3: draft ===")
    drafted_count = draft.draft_posts()
    log.info("Drafting produced %d drafted articles", drafted_count)

    if skip_send:
        log.info("--skip-send set. Stopping before digest.")
        return

    log.info("=== Stage 4: digest ===")
    sent_count = digest_module.send_digest()
    log.info("Digest sent for %d articles", sent_count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spin Control orchestrator")
    parser.add_argument(
        "stage",
        nargs="?",
        choices=["ingest", "score", "draft", "digest", "all"],
        default="all",
        help="Stage to run. Default: all.",
    )
    parser.add_argument(
        "--skip-send",
        action="store_true",
        help="When stage=all, run ingest/score/draft but not digest send.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level. Default: INFO.",
    )
    args = parser.parse_args(argv)

    configure_logging(args.log_level)

    if args.stage == "ingest":
        ingest.ingest()
    elif args.stage == "score":
        score.score_articles()
    elif args.stage == "draft":
        draft.draft_posts()
    elif args.stage == "digest":
        digest_module.send_digest()
    else:
        run_all(skip_send=args.skip_send)
    return 0


if __name__ == "__main__":
    sys.exit(main())
