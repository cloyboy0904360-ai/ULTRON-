from datetime import datetime, timezone

from ultron.research import (
    collect_opportunities,
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
    assert item["amount_basis"] == "advertised_unverified"


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


def test_expired_deadline_gets_zero_priority_score():
    item = {
        "title": "Old tender",
        "description": "A detailed contract notice from a council with a buyer.",
        "url": "https://example.org/old",
        "source_url": "https://example.org/feed.xml",
        "source_name": "Example",
        "published_at": "2026-10-01T00:00:00+00:00",
        "deadline": "2026-10-31",
    }
    assert score_opportunity(item, now=datetime(2026, 11, 18, tzinfo=timezone.utc)) == 0


def test_collection_isolates_feed_failure_and_keeps_successful_feed():
    xml = """<rss version="2.0"><channel><item>
      <title>Paid admin support</title><link>https://example.org/paid-admin</link>
      <guid>paid-admin-1</guid><description>Buyer: Example Ltd. Budget £100.</description>
    </item></channel></rss>"""

    def fetch(url):
        if url.endswith("broken.xml"):
            raise OSError("temporary source failure")
        return xml

    merged, errors, successful = collect_opportunities(
        [{"name": "Broken", "url": "https://example.org/broken.xml"},
         {"name": "Working", "url": "https://example.org/feed.xml"}],
        fetcher=fetch,
        now=datetime(2026, 11, 18, tzinfo=timezone.utc),
    )
    assert successful == 1
    assert len(errors) == 1 and "temporary source failure" in errors[0]
    assert len(merged) == 1
    assert merged[0]["amount_basis"] == "advertised_unverified"
