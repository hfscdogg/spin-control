"""Score articles against Livewire relevance themes using Claude Haiku."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import anthropic

import db
from config_loader import env, load_config

log = logging.getLogger("spin_control.score")

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "score.txt"

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "integer", "minimum": 1, "maximum": 10},
        "timeliness": {"type": "integer", "minimum": 1, "maximum": 10},
        "perspective": {"type": "integer", "minimum": 1, "maximum": 10},
        "traffic": {"type": "integer", "minimum": 1, "maximum": 10},
        "summary": {"type": "string"},
        "why_it_matters": {"type": "string"},
    },
    "required": [
        "relevance",
        "timeliness",
        "perspective",
        "traffic",
        "summary",
        "why_it_matters",
    ],
    "additionalProperties": False,
}


def _load_system_prompt(config: dict) -> str:
    base = PROMPT_PATH.read_text(encoding="utf-8").strip()
    themes = "\n".join(f"- {t}" for t in config["relevance_themes"])
    return f"{base}\n\nRelevance themes Livewire cares about:\n{themes}"


def _build_user_prompt(article) -> str:
    parts = [f"Title: {article['title']}", f"Source: {article['source']}"]
    if article["category"]:
        parts.append(f"Category: {article['category']}")
    if article["published_at"]:
        parts.append(f"Published: {article['published_at']}")
    if article["raw_summary"]:
        parts.append(f"Summary:\n{article['raw_summary']}")
    parts.append(f"URL: {article['url']}")
    return "\n".join(parts)


def score_articles() -> int:
    """Score every fetched-but-unscored article. Returns count scored."""
    env("ANTHROPIC_API_KEY", required=True)
    config = load_config()
    model = config["models"]["scoring"]
    system_prompt = _load_system_prompt(config)
    max_to_score = config["ingestion"]["max_to_score"]

    client = anthropic.Anthropic()

    scored = 0
    with db.connect() as conn:
        articles = db.fetch_unscored(conn, max_to_score)
        if not articles:
            log.info("No unscored articles")
            return 0
        log.info("Scoring %d articles with %s", len(articles), model)

        for article in articles:
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=1024,
                    system=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    output_config={
                        "format": {
                            "type": "json_schema",
                            "schema": SCORE_SCHEMA,
                        }
                    },
                    messages=[{"role": "user", "content": _build_user_prompt(article)}],
                )
            except anthropic.APIError as exc:
                log.warning("Scoring failed for article %s: %s", article["id"], exc)
                continue

            text_block = next(
                (b.text for b in response.content if b.type == "text"), None
            )
            if not text_block:
                log.warning("No text block in scoring response for %s", article["id"])
                continue
            try:
                payload = json.loads(text_block)
            except json.JSONDecodeError as exc:
                log.warning("Bad JSON from scorer for %s: %s", article["id"], exc)
                continue

            scores = {
                k: int(payload[k])
                for k in ("relevance", "timeliness", "perspective", "traffic")
            }
            total = sum(scores.values())

            db.save_score(
                conn,
                article_id=article["id"],
                scores=scores,
                summary=payload.get("summary", "")[:2000],
                why_it_matters=payload.get("why_it_matters", "")[:2000],
                total_score=total,
            )
            scored += 1
            log.info(
                "Scored %s [%d/40]: %s",
                article["id"],
                total,
                article["title"][:80],
            )

    log.info("Scoring complete: %d articles scored", scored)
    return scored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    score_articles()
