# Free Revenue Opportunity Research v1

## What it does

The collector uses Python's standard library only. It downloads configured public RSS/Atom feeds, parses each entry, assigns a deterministic evidence/actionability score, deduplicates records, stores the latest dataset as JSON, and writes a Markdown report.

It does not use Copilot APIs, an LLM, paid search APIs, paid connectors, browser automation, or external actions. Copilot may be used interactively to inspect the results and propose code improvements within the user's existing free allowance; scheduled runs do not call Copilot.

## Schedule

GitHub Actions runs once daily at 09:00 UTC and can also be started manually from Actions → Free revenue opportunity research → Run workflow. The repository is public. The workflow uses only the hosted runner's preinstalled Python and shell/Git/GitHub CLI; it uses no third-party Actions. It has a five-minute timeout and commits only the refreshed research data and report to the main branch.

The workflow has `contents: write` solely so it can persist its own data and report. It does not access secrets or modify customer, payment, marketplace or account settings. If GitHub Actions becomes unavailable or a policy blocks the run, no alternate paid service is started.

## Sources

The initial configuration is `config/research-feeds.json`. It contains four Google News RSS keyword searches for:
- UK freelance contracts and paid freelance work
- AI data annotation and training tasks
- UK small-business contracts and supplier opportunities
- UK public-sector tenders and SME opportunities

Google News RSS is a community-observed public interface, not a documented Google API. Its search links redirect through Google and may omit the original article body. Feeds can change or fail without notice. These are discovery leads only, not authoritative tender records. The original publisher or procurement notice must be opened and checked before any commercial decision.

Add, edit or disable sources in the JSON file. Each entry has `name`, `url`, and optional `enabled`. Only absolute HTTP(S) URLs are accepted. The collector reads no item links and does not crawl pages linked from a feed.

## Scoring

The 0–100 score measures evidence and actionability, not likelihood of winning, payment certainty, profit or suitability. Points are awarded for:
- source URL (15)
- item URL (20)
- useful title (10)
- substantive description (15)
- publication date (10)
- advertised GBP amount (10)
- stated deadline (10)
- named feed (5)
- buyer/client/employer indicator (5)
- publication within the last seven days (10)

The score is capped at 100. An expired deadline receives 0. Advertised amounts are explicitly marked `advertised_unverified`; they are never represented as earned income.

## Outputs

- `data/revenue-opportunities.json`: deduplicated opportunity records and first/last seen timestamps.
- `reports/revenue-opportunities.md`: ranked report, collection status, feed errors and mandatory verification checklist.
- GitHub Actions run summary: copy of the report for quick viewing.

The report deliberately says collected revenue is NOT CONNECTED. No payment ledger is queried by this first version. Do not substitute advertised contract values for actual settled revenue.

## Run locally

```bash
python3 -m ultron.research
python3 -m pytest
```

No dependency installation is needed for the collector itself. Tests use the repository's existing pytest development dependency.

## Safety and cost controls

- No AI provider or Copilot API is called by scheduled execution.
- No paid API, third-party action, marketplace subscription or paid data source is used.
- No secrets are read by the collector.
- Feed downloads have a 12-second timeout and a 1 MB response limit.
- Feed failures are isolated and recorded; one failed source does not suppress successful sources.
- No email, message, application, bid, listing, purchase, account change or customer contact is performed.
- The collector cannot estimate net profit because costs and platform fees are not available in RSS data.
- A lead must be independently verified before it is considered READY.
