# ULTRON Copilot R&D Instructions

Use Copilot as an interactive research and engineering assistant for ULTRON. Scheduled collection does not invoke Copilot, an LLM API, Copilot CLI, or an agent session.

## Cost boundary

- Use only Copilot features and usage already included in the user's current plan.
- Do not enable paid premium requests, buy credits, upgrade a plan, start a trial, or invoke a paid model.
- Do not add AI API keys, external paid search services, paid MCP servers, paid GitHub Actions, or third-party Actions.
- If the available free allowance is unavailable or exhausted, stop AI-assisted work and leave a clear note. Do not switch to a paid model or provider.
- The RSS collector and scheduled workflow must remain deterministic and must not call Copilot or any AI provider.

## Research procedure

When asked to review the latest opportunity report:

1. Read `reports/revenue-opportunities.md` and `data/revenue-opportunities.json`.
2. Treat each entry as a lead, not a verified job, contract, customer or payment.
3. Open the original publisher or buyer notice; do not rely on a Google News redirect alone.
4. Verify the current status, buyer, eligibility, deadline, payment terms and application route.
5. Calculate fees, costs, delivery time and realistic net return only when evidence is available.
6. Label unknown information UNVERIFIED. Never invent win probabilities, profit, availability or payment evidence.
7. Rank opportunities by evidence, suitability, net return and time to payment.
8. Return a short report with source links and recommended next action.

## Engineering procedure

- Inspect existing code before changing it.
- Add a focused failing test before changing behaviour; run it, implement the smallest fix, and rerun.
- Keep the collector standard-library-only unless a dependency is explicitly approved.
- Keep public RSS ingestion read-only. Never crawl item links automatically.
- Preserve duplicate detection, source attribution, timestamps and error records.
- Keep advertised opportunity values separate from received revenue and profit.
- Improve source quality and scoring based on verified outcomes, not speculative assumptions.
- Use a branch and pull request. Do not merge or deploy without review.

## External-action boundary

Copilot may research, compare, draft, calculate and recommend. It must not send customer messages, apply for work, bid, publish listings, sign contracts, spend money, change payment settings or alter external accounts without explicit user approval.
