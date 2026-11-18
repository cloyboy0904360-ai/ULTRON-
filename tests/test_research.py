from datetime import datetime, timezone

from ultron.research import (
    merge_opportunities,
    parse_rss,
    render_report,
    score_opportunity,
)


def test_parse_rss_extracts_source_and_stable_identity():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Example opportunities</title>
      <item><title>Paid website redesign contract</title>
        <link>https://example.org/opportunity/42</link>
        <guid>notice-42</guid>
        <description><![CDATA[Buyer: Example Council. Budget £2,500. Closes 30 November 2026.]]></description>
        <pubDate>Wed, 18 Nov 2026 09:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    items = parse_rss(xml, source_name="Example", source_url="https://example.org/feed.xml")
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "Paid website redesign contract"
    assert item["url"] == "https://example.org/opportunity/42"
    assert item["source_url"] == "https://example.org/feed.xml"
    assert item["external_id"] == "notice-42"
    assert item["amount_pence"] == 250000
    assert item["currency"] == "GBP"
    assert item["published_at"] == "2026-11-18T09:00:00+00:00"


def test_scoring_is_deterministic_and_does_not_invent_probability_or_profit():
    item = {
        "title": "Paid bookkeeping support contract",
        "description": "Buyer: Local Council. Budget £1,200. Deadline 30 November 2026.",
        "url": "https://example.org/notice/1",
        "source_url": "https://example.org/feed.xml",
        "published_at": "2026-11-18T09:00:00+00:00",
        "amount_pence": 120000,
    }
    first = score_opportunity(item, now=datetime(2026, 11, 18, tzinfo=timezone.utc))
    second = score_opportunity(item, now=datetime(2026, 11, 18, tzinfo=timezone.utc))
    assert first == second
    assert 0 <= first <= 100
    assert "win_probability" not in item
    assert "net_profit_pence" not in item


def test_merge_deduplicates_by_stable_id_and_keeps_newest_observation():
    older = {"id": "same", "title": "Old title", "first_seen_at": "2026-11-17T00:00:00+00:00"}
    newer = {"id": "same", "title": "Updated title", "first_seen_at": "2026-11-18T00:00:00+00:00"}
    merged = merge_opportunities([older], [newer, {"id": "other", "title": "Other"}])
    assert len(merged) == 2
    assert next(x for x in merged if x["id"] == "same")["title"] == "Updated title"


def test_report_separates_opportunity_value_from_collected_revenue():
    report = render_report(
        [{"id": "1", "title": "Contract", "url": "https://example.org/1",
          "source_name": "Example", "score": 75, "amount_pence": 100000,
          "currency": "GBP", "status": "DISCOVERED"}],
        collected_revenue_pence=0,
        collection_status="SUCCESS",
        errors=[],
    )
    assert "£1,000.00" in report
    assert "£0.00" in report
    assert "potential" in report.lower()
    assert "collected revenue" in report.lower()
