"""Generate three social-post variants per qualifying article using Claude Opus."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import anthropic

import db
from config_loader import env, load_config

log = logging.getLogger("spin_control.draft")

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "draft.txt"

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "linkedin_long": {"type": "string"},
        "linkedin_short": {"type": "string"},
        "social_caption": {"type": "string"},
    },
    "required": ["linkedin_long", "linkedin_short", "social_caption"],
    "additionalProperties": False,
}

# Keyword groups searched against title + summary + category to choose a CTA.
CTA_KEYWORDS = {
    "smart_home": ["smart home", "automation", "connected home", "home automation"],
    "commercial": ["commercial", "office", "workplace", "conference", "corporate"],
    "security": [
        "security",
        "surveillance",
        "camera",
        "alarm",
        "ai detection",
        "false positive",
        "intrusion",
    ],
    "lighting": ["lighting", "ketra", "circadian", "fixture", "tunable"],
    "networking": ["network", "wi-fi", "wifi", "router", "mesh", "connectivity"],
    "audio_video": ["audio", "video", "av ", "speaker", "theater", "tv ", "display"],
    "privacy": ["privacy", "data breach", "data leak", "spying", "tracker"],
}


def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def choose_cta(article: dict, cta_urls: dict) -> tuple[str, str]:
    """Return (keyword, url) for the article. Falls back to default."""
    haystack = " ".join(
        filter(
            None,
            [
                (article.get("title") or "").lower(),
                (article.get("summary") or "").lower(),
                (article.get("category") or "").lower(),
                (article.get("why_it_matters") or "").lower(),
            ],
        )
    )
    for keyword, terms in CTA_KEYWORDS.items():
        if any(term in haystack for term in terms):
            return keyword, cta_urls.get(keyword, cta_urls["default"])
    return "default", cta_urls["default"]


_EM_DASH_RE = re.compile(r"[—–]")
_OXFORD_RE = re.compile(r",\s+(?:and|or)\s+\w")


def voice_check(text: str) -> list[str]:
    """Return a list of voice-rule violations. Empty list = clean."""
    issues = []
    if _EM_DASH_RE.search(text):
        issues.append("contains em-dash or en-dash")
    if _OXFORD_RE.search(text):
        # The regex catches plausible Oxford-comma patterns. False positives are
        # acceptable here since the worst case is a flag in the digest, not a
        # blocked send.
        issues.append("possible Oxford comma")
    return issues


def _build_user_prompt(article: dict, cta_url: str) -> str:
    return (
        f"Title: {article['title']}\n"
        f"Source: {article['source']}\n"
        f"URL: {article['url']}\n\n"
        f"Summary:\n{article.get('summary') or article.get('raw_summary') or ''}\n\n"
        f"Why it matters to Livewire:\n{article.get('why_it_matters') or ''}\n\n"
        f"Suggested CTA URL (use exactly once per variant, on its own final line):\n{cta_url}"
    )


def draft_posts() -> int:
    """Draft posts for every qualifying article. Returns number drafted."""
    env("ANTHROPIC_API_KEY", required=True)
    config = load_config()
    model = config["models"]["drafting"]
    threshold = config["scoring"]["threshold"]
    max_articles = config["digest"]["max_articles"]
    cta_urls = config["cta_urls"]
    system_prompt = _load_system_prompt()

    client = anthropic.Anthropic()

    drafted = 0
    with db.connect() as conn:
        articles = db.fetch_qualifying_for_drafting(conn, threshold, max_articles)
        if not articles:
            log.info("No qualifying articles to draft")
            return 0
        log.info(
            "Drafting %d articles (>= %d/40) with %s",
            len(articles),
            threshold,
            model,
        )

        for article in articles:
            article_dict = dict(article)
            cta_keyword, cta_url = choose_cta(article_dict, cta_urls)

            try:
                with client.messages.stream(
                    model=model,
                    max_tokens=4096,
                    thinking={"type": "adaptive"},
                    output_config={
                        "format": {
                            "type": "json_schema",
                            "schema": DRAFT_SCHEMA,
                        }
                    },
                    system=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[
                        {"role": "user", "content": _build_user_prompt(article_dict, cta_url)}
                    ],
                ) as stream:
                    response = stream.get_final_message()
            except anthropic.APIError as exc:
                log.warning("Drafting failed for article %s: %s", article["id"], exc)
                continue

            text_block = next(
                (b.text for b in response.content if b.type == "text"), None
            )
            if not text_block:
                log.warning("No text block in draft response for %s", article["id"])
                continue
            try:
                payload = json.loads(text_block)
            except json.JSONDecodeError as exc:
                log.warning("Bad JSON from drafter for %s: %s", article["id"], exc)
                continue

            draft_rows: list[tuple[str, str, str]] = []
            for variant_key in ("linkedin_long", "linkedin_short", "social_caption"):
                content = payload.get(variant_key, "").strip()
                if not content:
                    continue
                issues = voice_check(content)
                if issues:
                    log.warning(
                        "Voice issues on article %s variant %s: %s",
                        article["id"],
                        variant_key,
                        ", ".join(issues),
                    )
                draft_rows.append((variant_key, content, cta_url))

            if not draft_rows:
                log.warning("No usable variants for article %s", article["id"])
                continue

            db.save_drafts(conn, article["id"], draft_rows)
            drafted += 1
            log.info(
                "Drafted %s [%s -> %s]: %s",
                article["id"],
                cta_keyword,
                cta_url,
                article["title"][:80],
            )

    log.info("Drafting complete: %d articles", drafted)
    return drafted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    draft_posts()
