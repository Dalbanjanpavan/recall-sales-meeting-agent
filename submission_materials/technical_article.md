# I Built a Sales Agent That Uses Hindsight to Remember Customer History

## What Recall is

Recall is a small Flask web application for sales teams who need to prepare for follow-up meetings. A salesperson enters a prospect's name, company, meeting goal, and notes. Recall looks at the current notes together with earlier memories, builds a picture of the deal, points out conflicts or uncertainty, and suggests a next action.

The important part is that the suggestion is not supposed to become a real task automatically. Recall creates a proposal first. A person reviews it and either rejects it or approves it. Only after approval does the backend create a task. When the task has a confirmed outcome, that outcome can be saved locally and retained in Hindsight for a later meeting.

This makes the project more than a meeting summarizer. It tries to preserve the history of a customer relationship: what was requested, what the sales team promised, what was actually sent or received, what is still uncertain, and what has already been completed.

## Why I built it

Sales notes become difficult to use after several meetings. A proposal can be promised in one meeting, discussed in another, and confirmed as received in a third. A normal summary may repeat all three statements without deciding which one is current. It may also turn a request into a confirmed appointment or treat an old deadline as active.

I wanted to explore whether an AI agent could help with this kind of continuity while still leaving important decisions with a human. The interesting problem was not just asking a model to write a nice summary. It was making the surrounding system careful enough that the summary had evidence behind it and did not silently change the meaning of an old note.

## How the workflow works

The browser sends meeting information to the Flask `/api/prepare` endpoint. The backend creates a stable prospect ID from the person's name and company. That ID is used when retrieving memory, loading structured state from SQLite, and saving the new meeting.

The workflow is roughly:

1. Validate the submitted fields and check that the notes do not appear to describe a different person.
2. Recall earlier memories for the selected prospect.
3. Filter and deduplicate the retrieved text while preserving different dated events.
4. Detect conflicts, especially around receipt claims, deadlines, approvals, and customer status.
5. Ask the AI service to extract state, lifecycle information, ownership, evidence, and a next-best action.
6. Apply deterministic Python checks after the model responds.
7. Save the structured customer state and create a pending task proposal if appropriate.
8. Show the briefing and evidence in the frontend.
9. Wait for a human approval before creating a task.
10. Save a confirmed outcome locally and attempt to retain it in Hindsight.

The final step is deliberately resilient. If Hindsight is down, the SQLite outcome is still the local record and the UI can expose a retry state. A failed external sync should not erase the result of work that a salesperson already recorded.

## Hindsight memory in Recall

Hindsight is used as the long-term memory layer. Each prospect gets a normalized ID and a tag such as `prospect:...`. Recall first asks Hindsight for memories with that tag. It then filters the returned records in the application as a second safety boundary.

That second filter matters. Two people can have similar names, and two companies can have similar names. An empty result is safer than mixing the history of the wrong customer into a recommendation. The memory processor also removes semantic duplicates, but it does not delete genuinely different events just because they use similar words. For example, “proposal promised” and “proposal received” are separate facts.

Confirmed task outcomes use a stable document ID based on the task ID. That gives retries an idempotent identity instead of creating a new memory every time a network request is repeated.

One of the most useful design decisions was making Hindsight lazy and optional. Importing Flask does not create a bank or make a network request. External calls have configured timeouts and retry limits. When credentials are missing or a service is unavailable, Recall can still run its local workflow with deterministic reasoning.

Here is the application factory from `app.py`. The same database object is injected into the agent and exposed to the routes, which prevents the agent and customer-state endpoint from accidentally reading different databases:

```python
def create_app(
    db_manager=None,
    hindsight_service=None,
    ai_service=None,
):
    """Create a Recall Flask app with injectable persistence and services."""
    flask_app = Flask(__name__)
    configured_db = db_manager or DatabaseManager()
    configured_hindsight = hindsight_service or HindsightService()
    configured_ai = ai_service or AIService()
    configured_agent = RecallAgent(
        hindsight_service=configured_hindsight,
        ai_service=configured_ai,
        db_manager=configured_db,
    )
    flask_app.extensions["recall_db"] = configured_db
    flask_app.extensions["recall_agent"] = configured_agent
    flask_app.register_blueprint(routes)
    return flask_app
```

## The technology and architecture

The frontend is a small HTML, CSS, and JavaScript interface. Flask provides the web server and JSON endpoints. The Python services are separated by responsibility:

- `agent_service.py` coordinates the full meeting workflow.
- `ai_service.py` calls Groq and contains the deterministic fallback.
- `hindsight_service.py` handles recall and retention.
- `memory_processor.py` handles identity filtering, cleanup, deduplication, and conflicts.
- `date_validator.py` checks calendar claims in Python.
- `database.py` manages SQLite persistence and audit history.
- `task_service.py` enforces approval, state transitions, outcomes, and retryable sync.

SQLite stores structured state such as decision stages, document lifecycle, unresolved actions, proposals, tasks, status history, and sync status. Hindsight stores the longer-form memory that can be recalled in a future meeting. The two systems have different jobs: SQLite is the local operational record, while Hindsight is the long-term conversational memory.

The AI service is multi-stage in spirit. It extracts facts and chronology, identifies decision stages, tracks a document lifecycle, considers ownership and uncertainty, and proposes a next action. The backend still has the final say on dates and safety rules. In particular, a model cannot turn a vague “by Friday” into an approved deadline by itself.

The Hindsight service uses a strict tag when it recalls memories:

```python
tag = f"prospect:{p_id}"
result = self.client.recall(
    bank_id=BANK_ID,
    query=query.strip(),
    tags=[tag],
    tags_match="all_strict",
)
```

## Interesting challenges and lessons

The hardest problems were not the first API calls. They were the small ambiguities that look harmless in a demo. “The CFO reviewed the quote” does not necessarily mean “the revised proposal was sent and received.” A requested meeting is not a scheduled meeting until there is confirmation. “Nope” is not a useful structured outcome. A completed task should not become pending again just because a user clicks an old control.

I learned that an AI output needs a deterministic reconciliation layer. In Recall, verified receipt evidence can remove a stale delivery recommendation and replace it with a final authorization meeting when the CFO or procurement step is still pending. The code also keeps proposal lifecycle states separate rather than treating them as one general status.

This is an example of that reconciliation idea from `agent_service.py`:

```python
if proposal_receipt and final_authorization_pending:
    recommendation.update({
        "recommended_action": (
            f"Schedule a meeting with {prospect_name}, the CFO, "
            "and Sales for final purchase authorization"
        ),
        "why_this_action": (
            "The proposal receipt is confirmed and the remaining "
            "verified step is final commercial authorization."
        ),
    })
```

The approval gate is also backend-enforced. The browser can display an approval button, but the server checks the proposal's current status before creating a task:

```python
proposal = self.db.get_proposal(proposal_id)
if not proposal:
    raise ValueError(f"Proposal '{proposal_id}' not found.")

if proposal["status"] != "pending":
    raise ValueError(
        f"Proposal '{proposal_id}' is already {proposal['status']}. "
        "Cannot execute again."
    )
```

This project also taught me to be honest about integration status. Automated tests can mock Groq and Hindsight and still provide useful coverage, but mocks do not prove that live credentials, network access, or an external account are working. The current project documentation records live behavior as unverified in this environment.

## What I would improve next

The current application is a focused demonstration rather than a production CRM. It uses SQLite, has no user authentication, and still depends on manual review for ambiguous dates and contradictory evidence. A next version could add authenticated users, a proper server database, richer memory inspection, and a clearer first-run configuration screen. I would also add a checked-in dependency lock or requirements file so setup is one command.

For now, the part I am happiest with is the boundary between suggestion and action. Recall can help organize a complicated customer history, but a human still decides whether a proposed follow-up should become a real task.

## Suggested visuals for the published article

The article can be published without images, but the submission guide recommends adding visuals. If I add them later, I would use:

- A screenshot of the Meeting Intelligence screen showing the meeting history sidebar.
- A screenshot of an evidence-backed recommendation with its lifecycle and uncertainty fields.
- The architecture diagram from `architecture.md`.

These should use synthetic customer data only. I would not include API keys, `.env` files, the local database, or private customer information.

