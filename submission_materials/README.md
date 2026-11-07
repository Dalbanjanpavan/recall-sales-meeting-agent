# Recall – AI Sales Agent

Recall is a Flask web application that helps a sales team prepare for customer follow-ups. It combines current meeting notes with isolated prospect memory, identifies contradictions and uncertainty, tracks proposal lifecycle, and recommends an evidence-backed next action. A human must approve a proposal before it becomes a task.

## Features

- Prospect-specific Hindsight recall with application-level filtering.
- Chronological memory cleanup and semantic deduplication.
- Contradiction detection for dates, receipt claims, approvals, and status.
- Separate document states such as requested, promised, sent, delivered, received, reviewed, approved, and rejected.
- Groq reasoning with a deterministic local fallback.
- SQLite customer state, proposals, tasks, audit history, and sync status.
- Backend-enforced approval and duplicate prevention.
- Outcome recording with retryable, idempotent Hindsight synchronization.
- Frontend displays for evidence, uncertainty, conflicts, lifecycle, tasks, and sync failures.

## Technologies

- Python 3.10+
- Flask
- SQLite
- Hindsight Python client
- Groq Python client
- HTML, CSS, and JavaScript
- Python `unittest` and mocked external services for automated tests

There is currently no checked-in `requirements.txt`. The imports used by the application require Flask, `python-dotenv`, `groq`, and the Hindsight client package, plus their dependencies.

## Architecture

The browser sends notes to Flask. `RecallAgent` coordinates Hindsight recall, SQLite state, AI reasoning, and deterministic safety checks. The AI suggests structured information and a task proposal; it does not execute the task. `TaskService` requires backend approval, writes the task to SQLite, and records the outcome locally before attempting Hindsight retention.

See [architecture.md](architecture.md) for a Mermaid diagram.

## Hindsight integration

Each prospect receives a normalized identity and a `prospect:<id>` tag. Recall queries that tag with strict matching, then applies application-level filtering and deduplication. Confirmed outcomes use a stable `outcome-<task_id>` document ID so a retry does not intentionally create a second copy.

The Hindsight client and bank setup are lazy. Importing Flask does not provision a bank or make a network call. Timeouts and retry counts come from environment variables. If credentials or the service are unavailable, local SQLite persistence and deterministic reasoning can still operate.

## Installation and setup

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install Flask python-dotenv groq hindsight-client
Copy-Item .env.example .env
```

Fill in `.env` locally if live services are available:

```text
HINDSIGHT_API_KEY=
HINDSIGHT_BASE_URL=https://api.hindsight.vectorize.io
HINDSIGHT_TIMEOUT_SECONDS=10
HINDSIGHT_MAX_ATTEMPTS=2
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-20b
```

Do not commit `.env`, API keys, SQLite databases, `.venv`, caches, or private customer data. The root `.gitignore` excludes `.env`, `.venv/`, Python caches, and `*.db` files.

## Run the application

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

Open `http://127.0.0.1:5000`. Without working Groq or Hindsight credentials, the application can use its local fallback, but live integration success must be checked separately.

## Example workflow

1. Enter a prospect, company, meeting goal, and notes.
2. Review the briefing, retrieved evidence, conflicts, lifecycle, and proposed action.
3. Resolve any ambiguous deadline before approval.
4. Approve or reject the proposal.
5. Record a structured task outcome.
6. Check the local task status and Hindsight synchronization state.
7. Use a later meeting for the same prospect to recall the confirmed history.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -W error::SyntaxWarning -m compileall -q app.py agent_service.py ai_service.py database.py date_validator.py hindsight_service.py memory_processor.py task_service.py tests
node --check static\script.js
```

The project documentation records an earlier run with 69 tests passing. On the current review date, the suite still runs all 69 tests but 3 date-conflict tests fail because they assume that the bare phrase “by Friday” refers to Friday, 2026-11-06; the configured current date is later. This is a date-sensitive test maintenance issue, not a claim of a clean current run. The tests use temporary SQLite databases and mocked external services. Live Groq and Hindsight behavior is not claimed as verified here.

## Current limitations

- This is a demonstration application, not an authenticated multi-user production system.
- SQLite would need a server database and more concurrency review for production scale.
- Ambiguous dates, contradictory evidence, and commercially important actions still require human review.
- Live Groq and Hindsight behavior depends on credentials, network access, account configuration, and service availability.
- A dependency lock or checked-in requirements file has not yet been added.

