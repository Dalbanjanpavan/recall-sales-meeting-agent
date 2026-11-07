"""
Database persistence layer for Recall — Sales Meeting Intelligence Agent.
Uses SQLite to store structured customer state, proposed tasks, created tasks, and approval logs.
Provides idempotent transactions, status transition enforcement, and sync state tracking.
"""

import os
import json
import sqlite3
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recall.db")


def get_db_connection(db_path: str = DB_FILE) -> sqlite3.Connection:
    """Create a database connection with row factory enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_FILE) -> None:
    """Initialize or migrate database tables for customer states, tasks, and audit logs."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # 1. Customer structured state table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS customer_state (
        prospect_id TEXT PRIMARY KEY,
        prospect_name TEXT NOT NULL,
        company TEXT NOT NULL,
        current_stage TEXT NOT NULL DEFAULT 'Discovery',
        requirements_json TEXT NOT NULL DEFAULT '[]',
        interests_json TEXT NOT NULL DEFAULT '[]',
        objections_json TEXT NOT NULL DEFAULT '[]',
        budget_concerns_json TEXT NOT NULL DEFAULT '[]',
        decisions_json TEXT NOT NULL DEFAULT '[]',
        customer_requests_json TEXT NOT NULL DEFAULT '[]',
        team_promises_json TEXT NOT NULL DEFAULT '[]',
        scheduled_actions_json TEXT NOT NULL DEFAULT '[]',
        completed_actions_json TEXT NOT NULL DEFAULT '[]',
        unresolved_actions_json TEXT NOT NULL DEFAULT '[]',
        decision_stages_json TEXT NOT NULL DEFAULT '[]',
        document_lifecycle_json TEXT NOT NULL DEFAULT '[]',
        conflicts_json TEXT NOT NULL DEFAULT '[]',
        last_confirmed_interaction TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    # 2. Task proposals table (AI proposed actions awaiting human review)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS task_proposals (
        proposal_id TEXT PRIMARY KEY,
        prospect_id TEXT NOT NULL,
        prospect_name TEXT NOT NULL,
        company TEXT NOT NULL,
        description TEXT NOT NULL,
        reason TEXT NOT NULL,
        supporting_evidence TEXT NOT NULL,
        due_date TEXT,
        responsible_person TEXT,
        requester TEXT,
        reviewer TEXT,
        commitment_maker TEXT,
        responsibility_source TEXT,
        evidence_hash TEXT,
        has_deadline_conflict INTEGER NOT NULL DEFAULT 0,
        deadline_conflict_details TEXT,
        confirmed_due_date TEXT,
        natural_language_deadline TEXT,
        deadline_source TEXT,
        status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'rejected', 'executed', 'expired'
        created_at TEXT NOT NULL,
        resolved_at TEXT
    )
    """)

    # 3. Follow-up tasks table (actual persistent tasks created upon human approval)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        proposal_id TEXT,
        prospect_id TEXT NOT NULL,
        prospect_name TEXT NOT NULL,
        company TEXT NOT NULL,
        description TEXT NOT NULL,
        reason TEXT NOT NULL,
        supporting_evidence TEXT NOT NULL,
        due_date TEXT,
        confirmed_due_date TEXT,
        natural_language_deadline TEXT,
        deadline_source TEXT,
        responsible_person TEXT,
        requester TEXT,
        reviewer TEXT,
        commitment_maker TEXT,
        responsibility_source TEXT,
        task_status TEXT NOT NULL DEFAULT 'Pending', -- 'Pending', 'In Progress', 'Completed', 'Cancelled'
        approval_status TEXT NOT NULL DEFAULT 'approved',
        outcome_note TEXT,
        outcome_recorded_at TEXT,
        hindsight_memory_id TEXT,
        hindsight_sync_status TEXT NOT NULL DEFAULT 'not_applicable', -- 'synced', 'failed', 'not_applicable'
        hindsight_sync_error TEXT,
        status_history_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    # 4. Approval decision audit log
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS approval_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proposal_id TEXT NOT NULL,
        task_id TEXT,
        decision TEXT NOT NULL, -- 'APPROVED', 'REJECTED'
        reason TEXT,
        timestamp TEXT NOT NULL
    )
    """)

    # Graceful migration for existing databases: check and add columns if missing
    def add_column_if_missing(table: str, column: str, col_type: str):
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [row["name"] for row in cursor.fetchall()]
        if column not in columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    add_column_if_missing("customer_state", "decision_stages_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column_if_missing("customer_state", "document_lifecycle_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column_if_missing("customer_state", "conflicts_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column_if_missing("task_proposals", "requester", "TEXT")
    add_column_if_missing("task_proposals", "reviewer", "TEXT")
    add_column_if_missing("task_proposals", "commitment_maker", "TEXT")
    add_column_if_missing("task_proposals", "responsibility_source", "TEXT")
    add_column_if_missing("task_proposals", "evidence_hash", "TEXT")
    add_column_if_missing("task_proposals", "has_deadline_conflict", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing("task_proposals", "deadline_conflict_details", "TEXT")
    add_column_if_missing("task_proposals", "confirmed_due_date", "TEXT")
    add_column_if_missing("task_proposals", "natural_language_deadline", "TEXT")
    add_column_if_missing("task_proposals", "deadline_source", "TEXT")
    add_column_if_missing("tasks", "requester", "TEXT")
    add_column_if_missing("tasks", "reviewer", "TEXT")
    add_column_if_missing("tasks", "commitment_maker", "TEXT")
    add_column_if_missing("tasks", "responsibility_source", "TEXT")
    add_column_if_missing("tasks", "confirmed_due_date", "TEXT")
    add_column_if_missing("tasks", "natural_language_deadline", "TEXT")
    add_column_if_missing("tasks", "deadline_source", "TEXT")
    add_column_if_missing("tasks", "hindsight_sync_status", "TEXT NOT NULL DEFAULT 'not_applicable'")
    add_column_if_missing("tasks", "hindsight_sync_error", "TEXT")
    add_column_if_missing("tasks", "status_history_json", "TEXT NOT NULL DEFAULT '[]'")

    conn.commit()
    conn.close()


class DatabaseManager:
    """Provides structured data access methods for Recall agent."""

    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        init_db(self.db_path)

    def get_conn(self) -> sqlite3.Connection:
        return get_db_connection(self.db_path)

    # ------------------ Customer State Operations ------------------

    def get_customer_state(self, prospect_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve the persisted customer state for a prospect."""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM customer_state WHERE prospect_id = ?", (prospect_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        return {
            "prospect_id": row["prospect_id"],
            "prospect_name": row["prospect_name"],
            "company": row["company"],
            "current_stage": row["current_stage"],
            "requirements": json.loads(row["requirements_json"] or "[]"),
            "interests": json.loads(row["interests_json"] or "[]"),
            "objections": json.loads(row["objections_json"] or "[]"),
            "budget_concerns": json.loads(row["budget_concerns_json"] or "[]"),
            "decisions": json.loads(row["decisions_json"] or "[]"),
            "customer_requests": json.loads(row["customer_requests_json"] or "[]"),
            "team_promises": json.loads(row["team_promises_json"] or "[]"),
            "scheduled_actions": json.loads(row["scheduled_actions_json"] or "[]"),
            "completed_actions": json.loads(row["completed_actions_json"] or "[]"),
            "unresolved_actions": json.loads(row["unresolved_actions_json"] or "[]"),
            "decision_stages": json.loads(row["decision_stages_json"] or "[]") if "decision_stages_json" in row.keys() else [],
            "document_lifecycle": json.loads(row["document_lifecycle_json"] or "[]") if "document_lifecycle_json" in row.keys() else [],
            "conflicts": json.loads(row["conflicts_json"] or "[]") if "conflicts_json" in row.keys() else [],
            "last_confirmed_interaction": row["last_confirmed_interaction"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def save_customer_state(self, state: Dict[str, Any]) -> None:
        """Insert or update customer state deterministically."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_conn()
        cursor = conn.cursor()

        cursor.execute("""
        INSERT INTO customer_state (
            prospect_id, prospect_name, company, current_stage,
            requirements_json, interests_json, objections_json, budget_concerns_json,
            decisions_json, customer_requests_json, team_promises_json,
            scheduled_actions_json, completed_actions_json, unresolved_actions_json,
            decision_stages_json, document_lifecycle_json, conflicts_json,
            last_confirmed_interaction, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(prospect_id) DO UPDATE SET
            prospect_name=excluded.prospect_name,
            company=excluded.company,
            current_stage=excluded.current_stage,
            requirements_json=excluded.requirements_json,
            interests_json=excluded.interests_json,
            objections_json=excluded.objections_json,
            budget_concerns_json=excluded.budget_concerns_json,
            decisions_json=excluded.decisions_json,
            customer_requests_json=excluded.customer_requests_json,
            team_promises_json=excluded.team_promises_json,
            scheduled_actions_json=excluded.scheduled_actions_json,
            completed_actions_json=excluded.completed_actions_json,
            unresolved_actions_json=excluded.unresolved_actions_json,
            decision_stages_json=excluded.decision_stages_json,
            document_lifecycle_json=excluded.document_lifecycle_json,
            conflicts_json=excluded.conflicts_json,
            last_confirmed_interaction=excluded.last_confirmed_interaction,
            updated_at=excluded.updated_at
        """, (
            state["prospect_id"],
            state.get("prospect_name", ""),
            state.get("company", ""),
            state.get("current_stage", "Discovery"),
            json.dumps(state.get("requirements", [])),
            json.dumps(state.get("interests", [])),
            json.dumps(state.get("objections", [])),
            json.dumps(state.get("budget_concerns", [])),
            json.dumps(state.get("decisions", [])),
            json.dumps(state.get("customer_requests", [])),
            json.dumps(state.get("team_promises", [])),
            json.dumps(state.get("scheduled_actions", [])),
            json.dumps(state.get("completed_actions", [])),
            json.dumps(state.get("unresolved_actions", [])),
            json.dumps(state.get("decision_stages", [])),
            json.dumps(state.get("document_lifecycle", [])),
            json.dumps(state.get("conflicts", [])),
            state.get("last_confirmed_interaction", now),
            state.get("created_at", now),
            now
        ))

        conn.commit()
        conn.close()

    # ------------------ Task Proposals Operations ------------------

    def create_proposal(self, proposal: Dict[str, Any]) -> Optional[str]:
        """
        Create a pending proposal record.
        Safeguards:
        1. Prevents duplicate pending proposals for the same action.
        2. Does NOT silently recreate rejected proposals if evidence/circumstances have not materially changed.
        """
        conn = self.get_conn()
        cursor = conn.cursor()

        desc_norm = proposal["description"].strip()
        prospect_id = proposal["prospect_id"]
        evidence = (proposal.get("supporting_evidence") or "").strip()
        evidence_hash = hashlib.sha256(f"{desc_norm}:{evidence}".encode("utf-8")).hexdigest()[:16]

        # Check for existing pending proposal with same description
        cursor.execute("""
            SELECT proposal_id FROM task_proposals 
            WHERE prospect_id = ? AND description = ? AND status = 'pending'
        """, (prospect_id, desc_norm))
        existing_pending = cursor.fetchone()
        if existing_pending:
            conn.close()
            return existing_pending["proposal_id"]

        # Check for previously rejected proposal with same evidence/circumstances
        cursor.execute("""
            SELECT proposal_id, evidence_hash FROM task_proposals 
            WHERE prospect_id = ? AND description = ? AND status = 'rejected'
            ORDER BY created_at DESC LIMIT 1
        """, (prospect_id, desc_norm))
        recent_rejected = cursor.fetchone()
        if recent_rejected:
            # If evidence hash matches or evidence has not changed, do NOT recreate rejected proposal
            if recent_rejected["evidence_hash"] == evidence_hash:
                conn.close()
                return None  # Preserves rejection without silent recreation

        now = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
        INSERT INTO task_proposals (
            proposal_id, prospect_id, prospect_name, company,
            description, reason, supporting_evidence,
            due_date, responsible_person, requester, reviewer,
            commitment_maker, responsibility_source,
            has_deadline_conflict, deadline_conflict_details,
            confirmed_due_date, natural_language_deadline, deadline_source,
            evidence_hash, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            proposal["proposal_id"],
            proposal["prospect_id"],
            proposal["prospect_name"],
            proposal["company"],
            desc_norm,
            proposal.get("reason", "").strip(),
            evidence,
            proposal.get("due_date"),
            proposal.get("responsible_person"),
            proposal.get("requester"),
            proposal.get("reviewer"),
            proposal.get("commitment_maker"),
            proposal.get("responsibility_source"),
            1 if proposal.get("has_deadline_conflict") else 0,
            proposal.get("deadline_conflict_details"),
            proposal.get("confirmed_due_date"),
            proposal.get("natural_language_deadline"),
            proposal.get("deadline_source"),
            evidence_hash,
            now
        ))
        conn.commit()
        conn.close()
        return proposal["proposal_id"]

    def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM task_proposals WHERE proposal_id = ?", (proposal_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    # ------------------ Tasks & Approval Operations ------------------

    def approve_proposal_and_create_task(
        self,
        proposal_id: str,
        task_id: str,
        confirmed_due_date: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Atomically approves a proposal and creates the persistent task.
        Guarantees that a proposal can only be approved once.
        If the proposal has an unresolved deadline conflict, requires human confirmation
        via confirmed_due_date before task execution.
        """
        conn = self.get_conn()
        cursor = conn.cursor()

        try:
            conn.execute("BEGIN TRANSACTION")

            cursor.execute("SELECT * FROM task_proposals WHERE proposal_id = ?", (proposal_id,))
            proposal = cursor.fetchone()

            if not proposal:
                conn.rollback()
                conn.close()
                return None

            if proposal["status"] != "pending":
                raise ValueError(f"Proposal {proposal_id} is already {proposal['status']}.")

            # SAFE TASK CREATION: Validate deadline conflict resolution
            has_conflict = bool(proposal["has_deadline_conflict"]) if "has_deadline_conflict" in proposal.keys() else False
            existing_confirmed = proposal["confirmed_due_date"] if "confirmed_due_date" in proposal.keys() else None

            final_due_date = confirmed_due_date or existing_confirmed or proposal["due_date"]
            deadline_source = "Natural language / meeting notes"

            if has_conflict:
                if not confirmed_due_date and not existing_confirmed:
                    raise ValueError(
                        f"Unresolved deadline conflict: Proposal '{proposal_id}' has conflicting deadline interpretations. "
                        f"Human confirmation is required. Please explicitly specify confirmed_due_date (e.g. '2026-11-13' or '2026-11-06')."
                    )
                deadline_source = "Human confirmed via approval gate"
            elif confirmed_due_date:
                deadline_source = "Human confirmed via approval gate"

            now = datetime.now(timezone.utc).isoformat()

            # Mark proposal as approved
            cursor.execute("""
                UPDATE task_proposals 
                SET status = 'approved', resolved_at = ?, confirmed_due_date = ?, deadline_source = ?
                WHERE proposal_id = ?
            """, (now, final_due_date, deadline_source, proposal_id))

            initial_history = json.dumps([{
                "from": None,
                "to": "Pending",
                "timestamp": now,
                "note": f"Task created upon human approval. Deadline source: {deadline_source}."
            }])

            # Insert approved task
            cursor.execute("""
            INSERT INTO tasks (
                task_id, proposal_id, prospect_id, prospect_name, company,
                description, reason, supporting_evidence,
                due_date, confirmed_due_date, natural_language_deadline, deadline_source,
                responsible_person, requester, reviewer, commitment_maker, responsibility_source,
                task_status, approval_status, status_history_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', 'approved', ?, ?, ?)
            """, (
                task_id,
                proposal_id,
                proposal["prospect_id"],
                proposal["prospect_name"],
                proposal["company"],
                proposal["description"],
                proposal["reason"],
                proposal["supporting_evidence"],
                final_due_date,
                final_due_date if (confirmed_due_date or existing_confirmed) else None,
                proposal["natural_language_deadline"] if "natural_language_deadline" in proposal.keys() else proposal["due_date"],
                deadline_source,
                proposal["responsible_person"],
                proposal["requester"] if "requester" in proposal.keys() else None,
                proposal["reviewer"] if "reviewer" in proposal.keys() else None,
                proposal["commitment_maker"] if "commitment_maker" in proposal.keys() else None,
                proposal["responsibility_source"] if "responsibility_source" in proposal.keys() else None,
                initial_history,
                now,
                now
            ))

            # Add to audit log
            cursor.execute("""
            INSERT INTO approval_logs (proposal_id, task_id, decision, reason, timestamp)
            VALUES (?, ?, 'APPROVED', ?, ?)
            """, (proposal_id, task_id, f"Approved by user (deadline: {final_due_date}, source: {deadline_source})", now))

            conn.commit()

            cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
            created_task = dict(cursor.fetchone())
            conn.close()
            return created_task

        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            raise

    def reject_proposal(self, proposal_id: str, reason: str = "Rejected by user") -> bool:
        """Marks a proposal as rejected and logs the rejection. Never creates a task."""
        conn = self.get_conn()
        cursor = conn.cursor()

        try:
            conn.execute("BEGIN TRANSACTION")
            cursor.execute("SELECT status FROM task_proposals WHERE proposal_id = ?", (proposal_id,))
            proposal = cursor.fetchone()

            if not proposal:
                conn.rollback()
                conn.close()
                return False

            if proposal["status"] != "pending":
                conn.rollback()
                conn.close()
                raise ValueError(f"Proposal {proposal_id} is already {proposal['status']}.")

            now = datetime.now(timezone.utc).isoformat()
            cursor.execute("""
                UPDATE task_proposals 
                SET status = 'rejected', resolved_at = ? 
                WHERE proposal_id = ?
            """, (now, proposal_id))

            cursor.execute("""
            INSERT INTO approval_logs (proposal_id, task_id, decision, reason, timestamp)
            VALUES (?, NULL, 'REJECTED', ?, ?)
            """, (proposal_id, reason, now))

            conn.commit()
            conn.close()
            return True

        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            raise

    def update_task_outcome(
        self,
        task_id: str,
        task_status: str,
        outcome_note: str,
        hindsight_memory_id: Optional[str] = None,
        hindsight_sync_status: str = "not_applicable",
        hindsight_sync_error: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Update a task's status and outcome note with strict transition validation:
        - Prevents Completed tasks from silently reverting to Pending.
        - Preserves status transition history.
        - Tracks Hindsight memory sync status and errors.
        """
        valid_statuses = {"Pending", "In Progress", "Completed", "Cancelled"}
        if task_status not in valid_statuses:
            raise ValueError(f"Invalid task status: '{task_status}'. Must be one of {valid_statuses}")

        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        task = cursor.fetchone()

        if not task:
            conn.close()
            raise ValueError(f"Task '{task_id}' not found.")

        current_status = task["task_status"]

        allowed_transitions = {
            "Pending": {"Pending", "In Progress", "Completed", "Cancelled"},
            "In Progress": {"In Progress", "Completed", "Cancelled"},
            "Completed": {"Completed"},
            "Cancelled": {"Cancelled"},
        }
        if task_status not in allowed_transitions.get(current_status, set()):
            conn.close()
            raise ValueError(
                f"Invalid status transition: {current_status} cannot change to {task_status}. "
                "Create a new follow-up task if work must resume after completion or cancellation."
            )

        now = datetime.now(timezone.utc).isoformat()

        # Update status history
        history = []
        if "status_history_json" in task.keys() and task["status_history_json"]:
            try:
                history = json.loads(task["status_history_json"])
            except Exception:
                history = []

        history.append({
            "from": current_status,
            "to": task_status,
            "timestamp": now,
            "note": outcome_note.strip() if outcome_note else None
        })

        cursor.execute("""
        UPDATE tasks 
        SET task_status = ?, outcome_note = ?, outcome_recorded_at = ?,
            hindsight_memory_id = COALESCE(?, hindsight_memory_id),
            hindsight_sync_status = ?, hindsight_sync_error = ?,
            status_history_json = ?, updated_at = ?
        WHERE task_id = ?
        """, (
            task_status,
            outcome_note.strip() if outcome_note else None,
            now,
            hindsight_memory_id,
            hindsight_sync_status,
            hindsight_sync_error,
            json.dumps(history),
            now,
            task_id
        ))

        conn.commit()

        cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_task_sync_status(self, task_id: str, status: str, error: Optional[str] = None, memory_id: Optional[str] = None):
        """Update Hindsight sync status for a task (used during retries)."""
        conn = self.get_conn()
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
        UPDATE tasks
        SET hindsight_sync_status = ?, hindsight_sync_error = ?,
            hindsight_memory_id = COALESCE(?, hindsight_memory_id), updated_at = ?
        WHERE task_id = ?
        """, (status, error, memory_id, now, task_id))
        conn.commit()
        conn.close()

    def _format_task_dict(self, row_dict: Dict[str, Any]) -> Dict[str, Any]:
        d = dict(row_dict)
        if d.get("confirmed_due_date"):
            d["is_date_confirmed"] = True
            d["display_deadline"] = d["confirmed_due_date"]
        else:
            d["is_date_confirmed"] = False
            d["display_deadline"] = "Deadline not confirmed"
            if d.get("due_date") and "2026-11-10" in str(d.get("due_date")):
                d["deadline_warning"] = "Historical data warning: stored deadline 2026-11-10 is invalid for the recorded Friday commitment and is not an active due date."
                d["unconfirmed_note"] = "Historical invalid deadline: 2026-11-10 (Tuesday, not Friday)"
        return d

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        conn.close()
        return self._format_task_dict(dict(row)) if row else None

    def list_tasks(self, prospect_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List created tasks, optionally filtered by prospect."""
        conn = self.get_conn()
        cursor = conn.cursor()
        if prospect_id:
            cursor.execute("SELECT * FROM tasks WHERE prospect_id = ? ORDER BY created_at DESC", (prospect_id,))
        else:
            cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [self._format_task_dict(dict(r)) for r in rows]

    def list_proposals(self, prospect_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self.get_conn()
        cursor = conn.cursor()
        query = "SELECT * FROM task_proposals WHERE 1=1"
        params = []
        if prospect_id:
            query += " AND prospect_id = ?"
            params.append(prospect_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
