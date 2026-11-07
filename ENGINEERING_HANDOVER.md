# Recall Engineering Handover

## Delivered changes

- Removed import-time Hindsight bank provisioning and added lazy, bounded client setup.
- Added `create_app()` with injectable database, Hindsight, and Groq services while preserving the existing default Flask app exports.
- Added startup regression tests for dependency injection and no-network service construction.
- Added `.env.example` with timeout and retry settings; real credentials remain local-only.
- Added `PROJECT_ARCHITECTURE.md` with the system diagram, data flow, memory cycle, reasoning cycle, and startup instructions.
- Preserved the existing memory, date, approval, lifecycle, retry, and fallback safeguards already covered by the audit suite.

## Verification status

Mocked verification covers the full workflow through Flask: prepare meeting, retrieve/clean memory, detect conflicts, create a pending proposal, reject or approve it, persist a task, record an outcome, and invoke Hindsight outcome retention. The integration test uses a temporary SQLite database.

Current automated result: **69 tests passed, 0 failed**. Python and frontend JavaScript syntax checks also pass. The original HTTP integration hang is resolved.

Live verification was attempted with a bounded, no-customer-data probe but did not return within the execution window and was stopped safely. Groq and Hindsight live behavior therefore remains **unverified** in this environment. The safe follow-up procedure is a bounded health/recall check plus clearly tagged temporary retention, followed by cleanup according to the Hindsight account's retention policy. Do not print API keys or claim live success from mocks.

## Test commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app.py agent_service.py ai_service.py database.py date_validator.py hindsight_service.py memory_processor.py task_service.py tests
```

The tests use temporary databases and mocked external clients. The checked-in `recall.db` is not used for test fixtures.

## Manual demonstration checklist

1. Start the app and open `/`.
2. Submit a prospect with notes containing a prior promise and an ambiguous deadline.
3. Confirm the briefing shows retrieved evidence, uncertainty, and conflicts.
4. Confirm the proposal shows requester, responsible party, evidence, and lifecycle context.
5. Attempt approval without a required confirmed deadline; confirm the backend rejects it.
6. Approve with an explicit date; confirm exactly one task is created.
7. Repeat approval; confirm no duplicate task is created.
8. Record a completed outcome; confirm SQLite status history and Hindsight sync status.
9. Simulate Hindsight failure; confirm the outcome remains saved locally and retry is offered.
10. Retry synchronization; confirm the stable outcome document ID prevents duplicate retention.
11. Submit a later meeting for the same prospect and confirm the outcome is recalled.
12. Submit a different prospect with similar company text and confirm isolation.

## Important files

- `app.py`: Flask routes and application factory.
- `agent_service.py`: workflow orchestration.
- `hindsight_service.py`: tagged memory retrieval and retention.
- `ai_service.py`: Groq reasoning and fallback reasoning.
- `memory_processor.py`: identity, isolation, deduplication, and conflicts.
- `date_validator.py`: deterministic date handling.
- `database.py`: SQLite schema, migrations, transactions, and audit history.
- `task_service.py`: approval, task lifecycle, outcomes, and sync retry.
- `tests/`: unit, audit, integration, and startup regression coverage.

## Remaining limitations

- Hindsight and Groq live behavior depends on credentials, service availability, account configuration, and network access.
- The UI is a single-page demo rather than an authenticated multi-user production system.
- SQLite is appropriate for the demonstration but would need a server database and concurrency review for production scale.
- Human review remains required for ambiguous dates, contradictory receipt claims, and any action with material commercial impact.

## Questions judges may ask

- How is prospect isolation enforced? Stable IDs, Hindsight tags, and application-level filtering are all applied.
- Can the model execute tasks? No. It only proposes; backend approval creates the task atomically.
- What happens if Hindsight fails? SQLite preserves the outcome and exposes retryable sync failure state.
- How are contradictions handled? They are retained and surfaced as conflicts rather than silently deleted.
- How are dates handled? Python validation controls weekday/date consistency and approval gating.
- How do you prevent duplicate outcomes? Outcome retention uses `outcome-<task_id>` as a stable document ID.
- What is mocked? Automated tests mock Groq and Hindsight; live verification is a separate explicitly reported activity.
