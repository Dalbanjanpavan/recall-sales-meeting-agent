"""
Recall — Sales Meeting Intelligence Agent
Flask Application Server
"""

import logging
from flask import Flask, Blueprint, current_app, render_template, request, jsonify
from hindsight_service import HindsightService
from ai_service import AIService
from database import DatabaseManager
from agent_service import RecallAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("recall_app")

routes = Blueprint("recall_routes", __name__)


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


def _current_agent():
    return current_app.extensions["recall_agent"]


def _current_db():
    return current_app.extensions["recall_db"]


@routes.route("/")
def home():
    """Render main application UI."""
    return render_template("index.html")


@routes.route("/api/prepare", methods=["POST"])
def prepare_meeting():
    """
    Main Agent Execution Route:
    Accepts prospect input, retrieves Hindsight memories, performs chronological
    reasoning, selects Next Best Action, prepares follow-up task tool proposal,
    and returns verified sales intelligence.
    """
    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({"error": "Invalid request data. JSON body required."}), 400

        name = str(data.get("name", "")).strip()
        company = str(data.get("company", "")).strip()
        notes = str(data.get("notes", "")).strip()
        goal = str(data.get("goal", "")).strip()
        meeting_date = str(data.get("meeting_date", "")).strip() or None

        if not all([name, company, notes, goal]):
            return jsonify({"error": "Please fill in all required fields."}), 400

        # Execute full agent workflow
        result = _current_agent().process_meeting(
            name=name,
            company=company,
            goal=goal,
            notes=notes,
            meeting_date=meeting_date,
        )

        return jsonify(result)

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        current_app.logger.exception("Meeting preparation agent workflow failed")
        return jsonify({"error": str(e)}), 500


@routes.route("/api/tasks", methods=["GET"])
def get_tasks():
    """Retrieve created persistent tasks, optionally filtered by prospect_id."""
    try:
        prospect_id = request.args.get("prospect_id")
        tasks = _current_agent().tasks.get_tasks_for_prospect(prospect_id=prospect_id)
        return jsonify({
            "success": True,
            "tasks": tasks,
            "count": len(tasks)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes.route("/api/tasks/approve", methods=["POST"])
def approve_task():
    """
    Human Approval Gate:
    Validates proposal ID and status on the backend.
    Requires explicit confirmed_due_date if an unresolved deadline conflict exists.
    Executes the task creation tool in SQLite.
    Never executes without explicit user approval.
    """
    try:
        data = request.get_json(silent=True) or {}
        proposal_id = str(data.get("proposal_id", "")).strip()
        confirmed_due_date = data.get("confirmed_due_date")
        if confirmed_due_date:
            confirmed_due_date = str(confirmed_due_date).strip()

        if not proposal_id:
            return jsonify({"error": "Missing required field: proposal_id"}), 400

        created_task = _current_agent().tasks.approve_task(
            proposal_id=proposal_id,
            confirmed_due_date=confirmed_due_date
        )
        return jsonify({
            "success": True,
            "message": "Task approved and created successfully.",
            "task": created_task
        })

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        current_app.logger.exception("Task approval failed")
        return jsonify({"error": str(e)}), 500


@routes.route("/api/tasks/reject", methods=["POST"])
def reject_task():
    """
    Human Rejection Gate:
    Marks proposal rejected. Never creates a task.
    """
    try:
        data = request.get_json(silent=True) or {}
        proposal_id = str(data.get("proposal_id", "")).strip()
        reason = str(data.get("reason", "Rejected by salesperson")).strip()

        if not proposal_id:
            return jsonify({"error": "Missing required field: proposal_id"}), 400

        result = _current_agent().tasks.reject_task(proposal_id=proposal_id, reason=reason)
        return jsonify(result)

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        current_app.logger.exception("Task rejection failed")
        return jsonify({"error": str(e)}), 500


@routes.route("/api/tasks/<task_id>/outcome", methods=["POST"])
def update_task_outcome(task_id):
    """
    Record task outcome and synchronize to Hindsight:
    Updates SQLite record and retains confirmed outcome into Hindsight memory.
    Handles partial failure gracefully.
    """
    try:
        data = request.get_json(silent=True) or {}
        status = str(data.get("status", "Completed")).strip()
        outcome_note = str(data.get("outcome_note", "")).strip()

        result = _current_agent().tasks.update_outcome(
            task_id=task_id,
            new_status=status,
            outcome_note=outcome_note
        )
        return jsonify(result)

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        current_app.logger.exception("Task outcome update failed")
        return jsonify({"error": str(e)}), 500


@routes.route("/api/tasks/<task_id>/retry-sync", methods=["POST"])
def retry_task_sync(task_id):
    """Retry synchronizing a task outcome with Hindsight memory."""
    try:
        result = _current_agent().tasks.retry_hindsight_sync(task_id=task_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@routes.route("/api/prospect/<prospect_id>/state", methods=["GET"])
def get_customer_state(prospect_id):
    """Retrieve persisted structured state for a prospect."""
    try:
        state = _current_db().get_customer_state(prospect_id)
        if not state:
            return jsonify({"error": "Prospect state not found"}), 404
        return jsonify({"success": True, "state": state})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------ Meeting History API ------------------

@routes.route("/api/meetings", methods=["GET"])
def list_meetings():
    try:
        return jsonify({"success": True, "meetings": _current_db().list_meeting_history()})
    except Exception:
        current_app.logger.exception("Could not list meeting history")
        return jsonify({"error": "Meeting history is unavailable right now."}), 500


@routes.route("/api/meetings/<meeting_id>", methods=["GET"])
def get_meeting(meeting_id):
    try:
        meeting = _current_db().get_meeting_history(meeting_id)
        if not meeting:
            return jsonify({"error": "Meeting not found."}), 404
        return jsonify({"success": True, "meeting": meeting})
    except Exception:
        current_app.logger.exception("Could not load meeting history record")
        return jsonify({"error": "Could not load this meeting."}), 500


# Backwards-compatible default application objects. Service construction is now
# local-only; Hindsight network work is lazy and bounded to request time.
app = create_app()
db = app.extensions["recall_db"]
hindsight = app.extensions["recall_agent"].hindsight
ai = app.extensions["recall_agent"].ai
agent = app.extensions["recall_agent"]


if __name__ == "__main__":
    app.run(
        debug=True,
        threaded=True,
        use_reloader=False
    )
