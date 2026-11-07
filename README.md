# Recall — Sales Meeting Intelligence Agent

Recall is a human-in-the-loop sales intelligence agent that remembers customer interactions, reconstructs chronological deal context, detects contradictions, recommends evidence-backed next actions, and learns from confirmed outcomes.

## Workflow

```text
Meeting notes
  → prospect identity
  → isolated Hindsight recall
  → deduplication and conflict detection
  → customer state and document lifecycle
  → Groq reasoning or deterministic fallback
  → next-best action
  → human approval
  → SQLite task
  → confirmed outcome
  → idempotent Hindsight retention
```

Recall never executes a proposed task without backend-enforced human approval.

## Features

- Strict prospect isolation using normalized identity, Hindsight tags, and application filtering.
- Chronological memory cleanup with semantic deduplication while preserving distinct events.
- Explicit proposal lifecycle: requested, promised, sent, delivered, received, reviewed, approved, and rejected.
- Contradiction detection for deadlines, receipt claims, approvals, and customer status.
- Deterministic timezone-aware date validation; the LLM does not calculate weekdays.
- Groq-powered structured reasoning with a deterministic offline fallback.
- SQLite persistence for customer state, proposals, tasks, approvals, outcomes, and audit history.
- Idempotent Hindsight outcome retention with failure tracking and retry support.
- Frontend evidence, uncertainty, lifecycle, approval, task, and synchronization status displays.

## Requirements

- Python 3.10 or newer.
- The dependencies installed in the included virtual environment or an equivalent environment.
- Optional Groq and Hindsight credentials for live integrations.

## Configuration

Copy `.env.example` to `.env` and fill in credentials locally:

```powershell
Copy-Item .env.example .env
```

Never commit `.env`, API keys, SQLite databases, or virtual environments. These are excluded by `.gitignore`.

## Run locally

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

Open `http://127.0.0.1:5000` in a browser.

When Groq or Hindsight is unavailable, Recall uses local deterministic reasoning and preserves local SQLite outcomes. Live service success must be verified separately from mocked tests.

## Test

Tests use temporary SQLite databases and mocked external services:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -W error::SyntaxWarning -m compileall -q app.py agent_service.py ai_service.py database.py date_validator.py hindsight_service.py memory_processor.py task_service.py tests
node --check static\script.js
```

The current verified suite contains 69 tests.

## Important files

- `app.py` — Flask application factory and API routes.
- `agent_service.py` — end-to-end orchestration and deterministic safety reconciliation.
- `ai_service.py` — Groq reasoning and fallback reasoning.
- `hindsight_service.py` — tagged memory recall and retention.
- `memory_processor.py` — identity filtering, cleanup, deduplication, and conflicts.
- `date_validator.py` — calendar and deadline validation.
- `database.py` — SQLite persistence, migrations, transactions, and audit history.
- `task_service.py` — proposal approval, task transitions, outcomes, and sync retry.
- `templates/` and `static/` — web interface.
- `tests/` — unit, audit, integration, and startup regression tests.

See [PROJECT_ARCHITECTURE.md](PROJECT_ARCHITECTURE.md) for the data-flow diagram and [ENGINEERING_HANDOVER.md](ENGINEERING_HANDOVER.md) for verification details and manual testing guidance.

## Security notes

- Do not expose `.env` or API keys in commits, screenshots, logs, or issue reports.
- Use a separate Hindsight bank for demonstrations and test data.
- Do not use the production database for automated tests.
- Human confirmation remains required for ambiguous dates, contradictory receipt claims, and commercially material actions.

## Current limitation

Automated mocked verification is complete. Live Groq and Hindsight behavior depends on the configured credentials, account, network, and external service availability and must be reported separately.
