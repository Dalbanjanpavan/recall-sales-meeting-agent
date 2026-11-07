# Recall Architecture

Recall is a sales-meeting intelligence agent, not a generic summarizer. Its durable loop is:

```text
Meeting notes
    │
    ▼
Flask /api/prepare
    │
    ├── Prospect identity (stable normalized ID)
    ├── Hindsight tagged recall
    │       └── application isolation → deduplication → conflict detection
    ├── SQLite customer state
    ├── Groq reasoning (or deterministic fallback)
    │       ├── facts and chronology
    │       ├── decision stages and document lifecycle
    │       ├── ownership and uncertainty
    │       └── evidence-backed next-best action
    ├── SQLite pending task proposal
    └── Hindsight meeting retention
             │
             ▼
Human approval / rejection
    │
    ├── approve → atomic SQLite task creation
    └── reject  → audit log only
             │
             ▼
Task outcome
    │
    ├── SQLite status/history/outcome is authoritative locally
    └── confirmed outcome → idempotent Hindsight retention
             │
             ▼
Future tagged recall and reasoning
```

## Components

- `app.py` exposes the Flask API and provides `create_app()` dependency injection. The default `app` and `agent` exports remain for compatibility.
- `agent_service.py` coordinates the complete workflow and combines deterministic safeguards with AI analysis.
- `hindsight_service.py` owns tagged retrieval, retention, prospect filtering, and outcome synchronization. Hindsight client and bank setup are lazy; importing the app performs no network request.
- `ai_service.py` calls Groq when available and falls back to deterministic rule-based reasoning when unavailable or when model output is invalid.
- `memory_processor.py` normalizes prospect identity, filters records, preserves distinct dated events, removes semantic duplicates, and detects conflicts.
- `date_validator.py` handles weekday/date validation, relative deadline interpretation, ambiguity, and conflict detection in Python.
- `database.py` persists customer state, task proposals, tasks, approval logs, status history, and Hindsight sync state in SQLite.
- `task_service.py` enforces approval gates, task idempotency, status transitions, outcome recording, and retryable Hindsight synchronization.
- `templates/index.html`, `static/script.js`, and `static/style.css` provide the demo UI for briefing, evidence, conflicts, approvals, tasks, outcomes, and retries.

## Memory cycle

Each prospect receives a stable normalized identity and a `prospect:<id>` Hindsight tag. Recall first queries that tag, then applies application-level filtering using the prospect name, company, tags, and metadata. Retrieved records are deduplicated without deleting distinct dates, promises, approvals, receipt claims, or outcomes. Contradictions are returned as uncertainty and remain available for review.

Meeting notes are retained as a tagged interaction. A human-confirmed task outcome is retained with a stable `outcome-<task_id>` document ID, making retries idempotent. SQLite records sync status so a local outcome is never lost when Hindsight is unavailable.

## Groq reasoning cycle

The model receives the current notes, retrieved memories, and prior structured state. It is asked to extract customer facts, chronological progression, decision stages, proposal/document lifecycle, ownership, conflicts, next-best action, and a task proposal. The backend then sanitizes dates, merges deterministic conflicts, prevents unsafe deadline propagation, and stores only structured customer state and a pending proposal. The model never directly executes a task.

The fallback path uses deterministic pattern matching for common sales states, deadlines, promises, approvals, and lifecycle evidence. It explicitly distinguishes conditional approval, purchase intent, final approval, signed contract, and completed purchase.

## Startup

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

Copy `.env.example` to `.env` and configure credentials locally. `HINDSIGHT_TIMEOUT_SECONDS` and `HINDSIGHT_MAX_ATTEMPTS` bound external calls. The application can run in offline/fallback mode when credentials or services are unavailable.
