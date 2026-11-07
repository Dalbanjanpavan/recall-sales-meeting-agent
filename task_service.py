"""
Task Management Service for Recall — Sales Meeting Intelligence Agent.
Handles:
- Lifecycle of AI-proposed actions (pending, approved, rejected, executed)
- Backend enforcement of human approval gate (idempotent, single execution)
- Prevention of duplicate or repeatedly recreated rejected proposals
- Strict task status transition validation
- Synchronization between SQLite tasks and Hindsight memory with resilient failure handling
- Outcome retry mechanism
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from database import DatabaseManager
from hindsight_service import HindsightService


class TaskService:
    """Manages AI task proposals, human approvals, task persistence, and outcome updates."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None, hindsight_service: Optional[HindsightService] = None):
        self.db = db_manager or DatabaseManager()
        self.hindsight = hindsight_service

    def propose_task(
        self,
        prospect_id: str,
        prospect_name: str,
        company: str,
        description: str,
        reason: str,
        supporting_evidence: str,
        due_date: Optional[str] = None,
        responsible_person: Optional[str] = None,
        requester: Optional[str] = None,
        reviewer: Optional[str] = None,
        commitment_maker: Optional[str] = None,
        responsibility_source: Optional[str] = None,
        has_deadline_conflict: bool = False,
        deadline_conflict_details: Optional[str] = None,
        confirmed_due_date: Optional[str] = None,
        natural_language_deadline: Optional[str] = None,
        deadline_source: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Record a proposed action from the AI agent awaiting human approval.
        Safeguards:
        - Prevents duplicate pending proposals.
        - Does NOT recreate previously rejected proposals if evidence has not changed.
        - Flags unresolved deadline conflicts and stores natural language deadline.
        - Returns None if the proposal should not be created.
        """
        if not description or not description.strip():
            return None

        proposal_id = f"PROP-{uuid.uuid4().hex[:8].upper()}"

        proposal_data = {
            "proposal_id": proposal_id,
            "prospect_id": prospect_id,
            "prospect_name": prospect_name,
            "company": company,
            "description": description.strip(),
            "reason": (reason or "Identified by AI reasoning engine").strip(),
            "supporting_evidence": (supporting_evidence or "Meeting notes analysis").strip(),
            "due_date": due_date.strip() if due_date else None,
            "responsible_person": responsible_person.strip() if responsible_person else "Sales Team",
            "requester": requester.strip() if requester else prospect_name,
            "reviewer": reviewer.strip() if reviewer else None,
            "commitment_maker": commitment_maker.strip() if commitment_maker else "Sales Team",
            "responsibility_source": responsibility_source.strip() if responsibility_source else "Meeting notes",
            "has_deadline_conflict": has_deadline_conflict,
            "deadline_conflict_details": deadline_conflict_details,
            "confirmed_due_date": confirmed_due_date,
            "natural_language_deadline": natural_language_deadline or due_date,
            "deadline_source": deadline_source or ("Explicitly confirmed" if confirmed_due_date else "Natural language notes"),
            "status": "pending"
        }

        # create_proposal handles deduplication and rejected proposal preservation
        actual_id = self.db.create_proposal(proposal_data)
        if not actual_id:
            return None

        return self.db.get_proposal(actual_id)

    def approve_task(self, proposal_id: str, confirmed_due_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Human Approval Gate (Backend Enforced):
        Validates proposal status on backend.
        Requires explicit confirmed_due_date if proposal has an unresolved deadline conflict.
        Idempotent: Only an approved proposal in 'pending' status can create a task.
        """
        if not proposal_id or not proposal_id.strip():
            raise ValueError("Proposal ID is required.")

        proposal = self.db.get_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' not found.")

        if proposal["status"] != "pending":
            raise ValueError(f"Proposal '{proposal_id}' is already {proposal['status']}. Cannot execute again.")

        task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
        created_task = self.db.approve_proposal_and_create_task(
            proposal_id=proposal_id,
            task_id=task_id,
            confirmed_due_date=confirmed_due_date
        )

        if not created_task:
            raise RuntimeError(f"Failed to create task for proposal '{proposal_id}'.")

        return created_task

    def reject_task(self, proposal_id: str, reason: str = "Rejected by salesperson") -> Dict[str, Any]:
        """
        Human Rejection Gate:
        Records human rejection in audit log. Never creates a persistent task.
        """
        if not proposal_id or not proposal_id.strip():
            raise ValueError("Proposal ID is required.")

        proposal = self.db.get_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' not found.")

        if proposal["status"] != "pending":
            raise ValueError(f"Proposal '{proposal_id}' is already {proposal['status']}.")

        self.db.reject_proposal(proposal_id=proposal_id, reason=reason)
        return {
            "proposal_id": proposal_id,
            "status": "rejected",
            "message": "Action rejected. No task was created."
        }

    def update_outcome(
        self,
        task_id: str,
        new_status: str,
        outcome_note: str
    ) -> Dict[str, Any]:
        """
        Updates task status (Pending, In Progress, Completed, Cancelled) and outcome note.
        Enforces status transition rules (e.g. Completed cannot silently return to Pending).
        Synchronizes confirmed outcome to Hindsight memory with partial failure handling.
        """
        task = self.db.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        note = (outcome_note or "").strip()
        placeholder_notes = {"no", "nope", "n/a", "na", "none", "nil", "test", "ok"}
        if note.lower() in placeholder_notes or len(note) < 4 and note:
            raise ValueError("Outcome note must describe a confirmed result; placeholders such as 'nope' are not valid outcomes.")
        if new_status == "Pending" and note:
            raise ValueError("A Pending task cannot record an outcome. Move it to In Progress, Completed, or Cancelled first.")
        if new_status in {"Completed", "Cancelled"} and not note:
            raise ValueError("Completed or Cancelled tasks require a meaningful outcome note.")

        hindsight_mem_id = task.get("hindsight_memory_id")
        sync_status = "not_applicable"
        sync_error = None
        hindsight_synced = False

        # Only terminal, confirmed outcomes are retained as durable memory.
        should_sync = new_status in {"Completed", "Cancelled"}

        if should_sync:
            if self.hindsight and getattr(self.hindsight, "is_connected", True):
                try:
                    retain_res = self.hindsight.save_confirmed_outcome(
                        prospect_id=task["prospect_id"],
                        prospect_name=task["prospect_name"],
                        company=task["company"],
                        task_description=task["description"],
                        outcome_note=note,
                        task_id=task_id
                    )
                    if hasattr(retain_res, "id"):
                        hindsight_mem_id = str(retain_res.id)
                    sync_status = "synced"
                    hindsight_synced = True
                except Exception as e:
                    sync_status = "failed"
                    sync_error = str(e)
                    hindsight_synced = False
            else:
                sync_status = "failed"
                sync_error = "Hindsight memory service is offline or unavailable."
                hindsight_synced = False

        # Update SQLite record
        updated_task = self.db.update_task_outcome(
            task_id=task_id,
            task_status=new_status,
            outcome_note=note,
            hindsight_memory_id=hindsight_mem_id,
            hindsight_sync_status=sync_status,
            hindsight_sync_error=sync_error
        )

        return {
            "success": True,
            "task": updated_task,
            "hindsight_synced": hindsight_synced,
            "hindsight_sync_status": sync_status,
            "hindsight_sync_error": sync_error,
            "warning": f"Outcome saved locally, but Hindsight memory sync failed: {sync_error}" if sync_status == "failed" else None
        }

    def retry_hindsight_sync(self, task_id: str) -> Dict[str, Any]:
        """
        Retries Hindsight memory retention for a task where sync previously failed.
        Prevents duplicate retention if already synced.
        """
        task = self.db.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        if task.get("hindsight_sync_status") == "synced" and task.get("hindsight_memory_id"):
            return {
                "success": True,
                "message": "Task outcome is already synchronized with Hindsight memory.",
                "task": task
            }

        if not self.hindsight or not getattr(self.hindsight, "is_connected", True):
            raise RuntimeError("Hindsight memory service is currently unavailable. Please retry later.")

        try:
            retain_res = self.hindsight.save_confirmed_outcome(
                prospect_id=task["prospect_id"],
                prospect_name=task["prospect_name"],
                company=task["company"],
                task_description=task["description"],
                outcome_note=task.get("outcome_note") or f"Marked as {task['task_status']}.",
                task_id=task_id
            )
            mem_id = str(retain_res.id) if hasattr(retain_res, "id") else None
            self.db.update_task_sync_status(task_id, status="synced", error=None, memory_id=mem_id)
            updated = self.db.get_task(task_id)
            return {
                "success": True,
                "message": "Hindsight memory synchronized successfully.",
                "task": updated
            }
        except Exception as e:
            self.db.update_task_sync_status(task_id, status="failed", error=str(e))
            raise RuntimeError(f"Retry failed: {e}")

    def get_tasks_for_prospect(self, prospect_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.list_tasks(prospect_id=prospect_id)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.db.get_task(task_id)
