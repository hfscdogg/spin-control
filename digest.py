"""Compose and send the daily review-queue digest email."""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

import db
from config_loader import env, load_config

log = logging.getLogger("spin_control.digest")


def _format_subject(prefix: str) -> str:
    return f"{prefix} {datetime.now().strftime('%a %b %d, %Y')}"


def _render_article_block(article: dict, drafts: list[dict]) -> str:
    scores = json.loads(article["scores_json"]) if article["scores_json"] else {}
    score_chips = " ".join(
        f"<span style='background:#eef;padding:2px 6px;border-radius:3px;margin-right:4px;font-size:12px;'>{escape(k)}: {v}</span>"
        for k, v in scores.items()
    )
    title = escape(article["title"] or "(untitled)")
    url = escape(article["url"])
    source = escape(article["source"] or "")
    summary = escape(article["summary"] or "")
    why = escape(article["why_it_matters"] or "")
    total = article["total_score"] or 0

    drafts_html = []
    variant_labels = {
        "linkedin_long": "LinkedIn long-form",
        "linkedin_short": "LinkedIn short-form",
        "social_caption": "Instagram / Facebook caption",
    }
    for d in drafts:
        label = variant_labels.get(d["variant"], d["variant"])
        body = escape(d["content"]).replace("\n", "<br>")
        cta = escape(d["cta_url"] or "")
        drafts_html.append(
            f"<div style='border-left:3px solid #cccccc;padding-left:10px;margin:10px 0;'>"
            f"<div style='font-weight:bold;color:#444;'>{escape(label)}</div>"
            f"<div style='font-family:Helvetica,Arial,sans-serif;font-size:14px;line-height:1.5;margin-top:6px;'>{body}</div>"
            f"<div style='font-size:12px;color:#888;margin-top:6px;'>CTA: {cta}</div>"
            f"</div>"
        )

    return (
        f"<div style='border:1px solid #eee;padding:16px;margin-bottom:24px;'>"
        f"<h3 style='margin:0 0 6px 0;'><a href='{url}'>{title}</a></h3>"
        f"<div style='color:#666;font-size:13px;margin-bottom:8px;'>{source} | total {total}/40</div>"
        f"<div style='margin-bottom:8px;'>{score_chips}</div>"
        f"<p style='margin:6px 0;'><strong>Summary:</strong> {summary}</p>"
        f"<p style='margin:6px 0;'><strong>Why it matters:</strong> {why}</p>"
        f"<div style='margin-top:12px;'>{''.join(drafts_html)}</div>"
        f"</div>"
    )


def _render_text_article_block(article: dict, drafts: list[dict]) -> str:
    lines = [
        f"# {article['title']}",
        f"{article['source']} | total {article['total_score']}/40",
        f"URL: {article['url']}",
        "",
        f"Summary: {article['summary']}",
        f"Why it matters: {article['why_it_matters']}",
        "",
    ]
    variant_labels = {
        "linkedin_long": "LinkedIn long-form",
        "linkedin_short": "LinkedIn short-form",
        "social_caption": "Instagram / Facebook caption",
    }
    for d in drafts:
        label = variant_labels.get(d["variant"], d["variant"])
        lines.append(f"--- {label} ---")
        lines.append(d["content"])
        lines.append(f"(CTA: {d['cta_url']})")
        lines.append("")
    return "\n".join(lines)


def compose_digest(config: dict) -> tuple[str, str, str, list[int]]:
    """Returns (subject, html_body, text_body, article_ids)."""
    max_articles = config["digest"]["max_articles"]
    subject = _format_subject(config["digest"]["subject_prefix"])

    with db.connect() as conn:
        articles = db.fetch_drafted_for_digest(conn, max_articles)
        if not articles:
            return subject, "", "", []

        html_blocks: list[str] = []
        text_blocks: list[str] = []
        article_ids: list[int] = []

        for article in articles:
            article_dict = dict(article)
            drafts = [dict(d) for d in db.fetch_drafts_for_article(conn, article["id"])]
            html_blocks.append(_render_article_block(article_dict, drafts))
            text_blocks.append(_render_text_article_block(article_dict, drafts))
            article_ids.append(article["id"])

    html = (
        "<div style='font-family:Helvetica,Arial,sans-serif;max-width:760px;margin:auto;'>"
        f"<h2>{escape(subject)}</h2>"
        f"<p style='color:#666;'>Articles below are ranked by total score. Approve, edit, or reject in your queue. Phase 2 will replace this email with a web review UI.</p>"
        + "".join(html_blocks)
        + "<hr><p style='color:#999;font-size:12px;'>Sent by Spin Control. Voice rules: no em dashes, no Oxford commas, hooks in Unicode bold for LinkedIn long-form.</p>"
        "</div>"
    )
    text = f"{subject}\n{'=' * len(subject)}\n\n" + "\n\n".join(text_blocks)
    return subject, html, text, article_ids


def _send_via_sendgrid(
    subject: str, html_body: str, text_body: str, sender: str, recipient: str
) -> None:
    api_key = env("SENDGRID_API_KEY", required=True)
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail

    message = Mail(
        from_email=sender,
        to_emails=recipient,
        subject=subject,
        html_content=html_body,
        plain_text_content=text_body,
    )
    sg = SendGridAPIClient(api_key)
    response = sg.send(message)
    if response.status_code >= 300:
        raise RuntimeError(f"SendGrid send failed: {response.status_code} {response.body!r}")


def _send_via_gmail(
    subject: str, html_body: str, text_body: str, sender: str, recipient: str
) -> None:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    creds_path = env("GMAIL_CREDENTIALS_PATH", "gmail_credentials.json")
    token_path = env("GMAIL_TOKEN_PATH", "gmail_token.json")

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    service = build("gmail", "v1", credentials=creds)
    message = MIMEMultipart("alternative")
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.attach(MIMEText(text_body, "plain"))
    message.attach(MIMEText(html_body, "html"))
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def send_digest() -> int:
    """Compose and send the digest. Returns the number of articles included."""
    config = load_config()
    subject, html_body, text_body, article_ids = compose_digest(config)
    if not article_ids:
        log.info("No drafted articles to send. Skipping digest.")
        return 0

    backend = (env("DIGEST_EMAIL_BACKEND", "sendgrid") or "sendgrid").lower()
    sender = env("DIGEST_FROM", required=True)
    recipient = env("DIGEST_TO", required=True)

    log.info("Sending digest with %d articles via %s to %s", len(article_ids), backend, recipient)
    if backend == "sendgrid":
        _send_via_sendgrid(subject, html_body, text_body, sender, recipient)
    elif backend == "gmail":
        _send_via_gmail(subject, html_body, text_body, sender, recipient)
    else:
        raise ValueError(
            f"Unknown DIGEST_EMAIL_BACKEND: {backend!r}. Use 'sendgrid' or 'gmail'."
        )

    with db.connect() as conn:
        db.mark_sent(conn, article_ids)
        db.record_digest_run(conn, len(article_ids), notes=f"backend={backend}")
    log.info("Digest sent. %d articles marked sent.", len(article_ids))
    return len(article_ids)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    send_digest()
