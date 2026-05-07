# Spin Control

Daily news monitoring and social-post drafting for Livewire (Richmond, VA residential and commercial technology integrator).

Each morning at ~6:30 AM ET, Spin Control:

1. Ingests articles from a configured RSS source list (and NewsAPI when a key is provided).
2. Filters and scores candidates against Livewire-relevant themes using `claude-haiku-4-5`.
3. Drafts three post variants per qualifying article using `claude-opus-4-7`, in Livewire's voice.
4. Sends a review-queue digest email so a human can approve and post.

## Architecture

- Python 3.11+
- SQLite for dedup, scoring history, and review-queue state
- Claude API: Haiku 4.5 for scoring (cost), Opus 4.7 for drafting (quality)
- SendGrid (default) or Gmail API for digest delivery
- GitHub Actions cron for scheduling

## Layout

```
spin-control/
├── ingest.py        # RSS + NewsAPI fetch, dedup against SQLite
├── score.py         # Claude Haiku scoring against relevance themes
├── draft.py         # Claude Opus three-variant post generation
├── digest.py        # Email composition and send
├── main.py          # Orchestration entry point
├── db.py            # SQLite helpers
├── config.yaml      # Sources, thresholds, CTA URL map
├── prompts/
│   ├── score.txt
│   └── draft.txt
├── .github/workflows/daily.yml
└── .claude/settings.json
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in ANTHROPIC_API_KEY and SENDGRID_API_KEY (or Gmail credentials).
```

## Run end-to-end

```bash
python main.py
```

Run individual stages:

```bash
python ingest.py   # fetch + dedup
python score.py    # score un-scored articles
python draft.py    # draft posts for qualifying articles
python digest.py   # compose and send digest email
```

## Voice rules

Applied to every drafted post:

- No em dashes (rewrite around them, never substitute).
- No Oxford commas.
- LinkedIn long-form hooks use Unicode Mathematical Sans-Serif Bold for emphasis.
- In-scene or punchy openings.
- Decisive verdict-driven closers.
- Sign-off when relevant: "Stay frosty, and see you in the field."

Voice rules are enforced both in the system prompt and via a post-generation check in `draft.py`.

## Scoring

Each article is scored 1-10 on four axes by `claude-haiku-4-5`:

- `relevance` — relevance to Livewire customers
- `timeliness` — news value and recency
- `perspective` — opportunity for Livewire to add unique insight
- `traffic` — potential to drive traffic back to getlivewire.com

The threshold for drafting is `28 of 40` (configurable in `config.yaml`).

## Daily schedule

`.github/workflows/daily.yml` runs `main.py` at 6:30 AM ET. The cron uses UTC, accounts for both EST and EDT, and the digest send time targets 7:00 AM ET.

## Phase 1 scope

This build covers ingestion, scoring, drafting, and digest email. Phases 2 and 3 (web review UI, engagement tracking, auto-publish) are out of scope here.
