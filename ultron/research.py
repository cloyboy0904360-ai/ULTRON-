"""Free, deterministic RSS opportunity collection, scoring and reporting.

No AI provider, paid API, third-party dependency, or external write is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlparse

MAX_FEED_BYTES = 1_000_000
FETCH_TIMEOUT_SECONDS = 12
USER_AGENT = "ULTRON-Revenue-Research/1.0 (+https://github.com/cloyboy0904360-ai/ULTRON-)"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split()) or " ".join(value.split())


def _child_text(node: ET.Element, *names: str) -> str:
    for child in list(node):
        if child.tag.rsplit("}", 1)[-1].lower() in names:
            if child.tag.rsplit("}", 1)[-1].lower() == "link":
                href = child.attrib.get("href")
                if href:
                    return href.strip()
            return "".join(child.itertext()).strip()
    return ""


def _parse_date(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return None


def _extract_amount(text: str) -> int | None:
    match = re.search(r"(?:£\s*|\bGBP\s*)([\d,]+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(round(float(match.group(1).replace(",", "")) * 100))
    except ValueError:
        return None


def _extract_deadline(text: str) -> str | None:
    match = re.search(
        r"(?:close[sd]?|deadline|closing date|submit by)\s*:?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _stable_id(external_id: str, link: str, title: str, source_url: str) -> str:
    identity = external_id or link or f"{title.casefold()}|{source_url}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def parse_rss(xml_text: str, *, source_name: str, source_url: str) -> list[dict]:
    """Parse RSS 2.0 or Atom entries without following item links."""
    root = ET.fromstring(xml_text)
    entries = [
        node for node in root.iter()
        if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
    ]
    parsed: list[dict] = []
    for node in entries:
        title = _clean_text(_child_text(node, "title"))
        link = _child_text(node, "link")
        if not link:
            link = _child_text(node, "guid", "id")
        external_id = _child_text(node, "guid", "id")
        description = _clean_text(
            _child_text(node, "description", "summary", "content", "encoded")
        )
        published = _parse_date(
            _child_text(node, "pubdate", "published", "updated", "date")
        )
        combined = f"{title} {description}"
        amount = _extract_amount(combined)
        item = {
            "id": _stable_id(external_id, link, title, source_url),
            "external_id": external_id or None,
            "title": title or "Untitled opportunity",
            "description": description,
            "url": link,
            "source_name": source_name,
            "source_url": source_url,
            "published_at": published,
            "deadline": _extract_deadline(combined),
            "amount_pence": amount,
            "currency": "GBP" if amount is not None else None,
            "amount_basis": "advertised_unverified" if amount is not None else None,
            "status": "DISCOVERED",
        }
        item["score"] = score_opportunity(item)
        parsed.append(item)
    return parsed


def score_opportunity(item: dict, *, now: datetime | None = None) -> int:
    """Score evidence/actionability (0-100), not win probability or profit."""
    score = 0
    if urlparse(str(item.get("source_url", ""))).scheme in {"http", "https"}:
        score += 15
    if urlparse(str(item.get("url", ""))).scheme in {"http", "https"}:
        score += 20
    if len(str(item.get("title", "")).strip()) >= 8:
        score += 10
    description = str(item.get("description", ""))
    if len(description) >= 80:
        score += 15
    if item.get("published_at"):
        score += 10
    if item.get("amount_pence") is not None:
        score += 10
    if item.get("deadline"):
        score += 10
    if item.get("source_name"):
        score += 5
    if re.search(
        r"\b(buyer|client|employer|organisation|organization|council|company)\b",
        description,
        re.I,
    ):
        score += 5
    reference = now or datetime.now(timezone.utc)
    deadline = item.get("deadline")
    if deadline:
        try:
            if datetime.fromisoformat(deadline).date() < reference.date():
                return 0
        except (TypeError, ValueError):
            pass
    published = item.get("published_at")
    if published:
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            age_days = (reference - dt).total_seconds() / 86400
            if 0 <= age_days <= 7:
                score += 10
        except (TypeError, ValueError):
            pass
    return min(score, 100)


def merge_opportunities(
    existing: Iterable[dict], incoming: Iterable[dict]
) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in existing:
        if item.get("id"):
            merged[item["id"]] = dict(item)
    for item in incoming:
        if not item.get("id"):
            continue
        previous = merged.get(item["id"], {})
        combined = {**previous, **item}
        first_seen = [
            x for x in (previous.get("first_seen_at"), item.get("first_seen_at")) if x
        ]
        if first_seen:
            combined["first_seen_at"] = min(first_seen)
        merged[item["id"]] = combined
    return sorted(
        merged.values(),
        key=lambda x: (
            -int(x.get("score", 0)),
            str(x.get("published_at") or ""),
            x.get("id", ""),
        ),
    )


def _money(pence: int | None) -> str:
    return f"£{pence / 100:,.2f}" if isinstance(pence, int) else "Not stated"


def render_report(
    opportunities: list[dict],
    *,
    collected_revenue_pence: int | None,
    collection_status: str,
    errors: list[str],
    feed_count: int = 0,
    collected_at: str | None = None,
) -> str:
    timestamp = collected_at or datetime.now(timezone.utc).isoformat()
    lines = [
        "# ULTRON Revenue Opportunity Research",
        "",
        f"- **Collection status:** {collection_status}",
        f"- **Collected at (UTC):** {timestamp}",
        f"- **Feeds configured:** {feed_count}",
        f"- **Unique opportunities retained:** {len(opportunities)}",
        "- **Scoring:** evidence/actionability score out of 100; not a probability of winning.",
        "",
        "## Financial distinction",
        "",
        "Potential opportunity values are unverified advertised leads, not collected revenue or profit.",
    ]
    if collected_revenue_pence is None:
        lines.append(
            "- **Collected revenue:** NOT CONNECTED — no payment ledger was queried by this collector."
        )
    else:
        lines.append(
            f"- **Collected revenue:** {_money(collected_revenue_pence)} "
            "(supplied by the caller; verify against settled payment evidence)."
        )
    lines.extend(["", "## Highest-ranked opportunities", ""])
    if not opportunities:
        lines.append("No opportunities collected. Check feed configuration and collection errors.")
    else:
        lines.extend(
            [
                "| Score | Opportunity | Advertised value (unverified) | Source |",
                "|---:|---|---:|---|",
            ]
        )
        for item in opportunities[:25]:
            title = (
                str(item.get("title", "Untitled"))
                .replace("|", "\\|")
                .replace("[", "\\[")
                .replace("]", "\\]")
            )
            url = str(item.get("url", ""))
            title_cell = (
                f"[{title}]({url})"
                if urlparse(url).scheme in {"http", "https"}
                else title
            )
            source = str(item.get("source_name", "Unknown")).replace("|", "\\|")
            lines.append(
                f"| {int(item.get('score', 0))} | {title_cell} | "
                f"{_money(item.get('amount_pence'))} | {source} |"
            )
    lines.extend(["", "## Collection errors", ""])
    lines.extend([f"- {error}" for error in errors] or ["- None"])
    lines.extend(
        [
            "",
            "## Required verification before acting",
            "",
            "1. Open the original notice and confirm it is current.",
            "2. Verify the buyer, eligibility, deadline and payment terms.",
            "3. Calculate costs, fees and delivery time before ranking it as commercially viable.",
            "4. Obtain approval before contacting a buyer, applying, bidding or committing resources.",
            "",
        ]
    )
    return "\n".join(lines)


def fetch_feed(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("feed URL must be an absolute HTTP(S) URL")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
        },
    )
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        payload = response.read(MAX_FEED_BYTES + 1)
    if len(payload) > MAX_FEED_BYTES:
        raise ValueError(f"feed exceeds {MAX_FEED_BYTES} bytes")
    return payload.decode("utf-8-sig")


def collect_opportunities(
    feeds: list[dict],
    existing: list[dict] | None = None,
    fetcher: Callable[[str], str] = fetch_feed,
    *,
    now: datetime | None = None,
) -> tuple[list[dict], list[str], int]:
    reference = now or datetime.now(timezone.utc)
    incoming: list[dict] = []
    errors: list[str] = []
    successful = 0
    for feed in feeds:
        name, url = str(feed.get("name", "Unnamed feed")), str(feed.get("url", ""))
        if feed.get("enabled", True) is False:
            continue
        try:
            items = parse_rss(fetcher(url), source_name=name, source_url=url)
            successful += 1
            for item in items:
                item["first_seen_at"] = reference.isoformat()
                item["last_seen_at"] = reference.isoformat()
                item["score"] = score_opportunity(item, now=reference)
                if item.get("deadline"):
                    try:
                        if datetime.fromisoformat(item["deadline"]).date() < reference.date():
                            item["status"] = "EXPIRED"
                    except ValueError:
                        pass
                incoming.append(item)
        except Exception as exc:  # isolate one broken feed from the rest
            errors.append(f"{name} ({url}): {type(exc).__name__}: {exc}")
    merged = merge_opportunities(existing or [], incoming)
    return merged, errors, successful


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect and score free RSS revenue opportunity feeds"
    )
    parser.add_argument("--feeds", default="config/research-feeds.json")
    parser.add_argument("--data", default="data/revenue-opportunities.json")
    parser.add_argument("--report", default="reports/revenue-opportunities.md")
    args = parser.parse_args(argv)
    feeds_path, data_path, report_path = map(
        Path, (args.feeds, args.data, args.report)
    )
    feeds = _read_json(feeds_path, [])
    existing = _read_json(data_path, [])
    now = datetime.now(timezone.utc)
    if not feeds:
        errors = [
            f"No enabled RSS feeds configured in {feeds_path}; add verified public feed URLs."
        ]
        status = "BLOCKED"
        merged = existing
        successful = 0
    else:
        merged, errors, successful = collect_opportunities(
            feeds, existing, now=now
        )
        status = (
            "SUCCESS" if successful and not errors
            else "PARTIAL" if successful
            else "FAILED"
        )
    report = render_report(
        merged,
        collected_revenue_pence=None,
        collection_status=status,
        errors=errors,
        feed_count=len([f for f in feeds if f.get("enabled", True)]),
        collected_at=now.isoformat(),
    )
    if successful or not feeds:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(report)
    return 0 if successful or not feeds else 1


if __name__ == "__main__":
    raise SystemExit(main())
