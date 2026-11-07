"""Startup and dependency-injection regressions for Recall."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from ai_service import AIService
from app import create_app
from database import DatabaseManager, init_db
from date_validator import redact_invalid_weekday_date_claims, resolve_relative_deadline
from hindsight_service import HindsightService
from memory_processor import clean_and_deduplicate_memories, detect_memory_conflicts, normalize_prospect_id
from agent_service import RecallAgent
from task_service import TaskService


class TestApplicationStartup(unittest.TestCase):
    def test_factory_uses_injected_services_and_database(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            init_db(path)
            db = DatabaseManager(path)
            hindsight = HindsightService(offline_mode=True)
            ai = AIService(offline_mode=True)
            app = create_app(db_manager=db, hindsight_service=hindsight, ai_service=ai)
            app.config["TESTING"] = True

            with app.test_client() as client:
                response = client.get("/api/tasks")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["tasks"], [])
            self.assertIs(app.extensions["recall_db"], db)
            self.assertIs(app.extensions["recall_agent"].hindsight, hindsight)
            self.assertIs(app.extensions["recall_agent"].ai, ai)
        finally:
            os.remove(path)

    def test_hindsight_client_is_not_called_during_service_construction(self):
        client = MagicMock()
        service = HindsightService(client=client)
        client.create_bank.assert_not_called()
        self.assertTrue(service.is_connected)

    def test_unicode_invalid_weekday_date_is_redacted_from_generated_prose(self):
        brief = "Delivery is due by Friday (2026‑11‑10)."
        cleaned = redact_invalid_weekday_date_claims(brief)
        self.assertIn("Deadline not confirmed", cleaned)
        self.assertNotIn("2026-11-10", cleaned)

    def test_semantic_deadline_conflicts_are_shown_once(self):
        conflicts = RecallAgent._deduplicate_conflicts([
            {"type": "conflicting_deadlines", "description": "Historical date conflicts with delivery required 'friday'."},
            {"type": "conflicting_deadlines", "description": "Historical date conflicts with delivery required 'by Friday'."},
        ])
        self.assertEqual(len(conflicts), 1)

    def test_proposal_receipt_and_unconfirmed_delivery_are_a_conflict(self):
        conflicts = detect_memory_conflicts([
            {"text": "The proposal was received by the CFO."},
            {"text": "Proposal delivery unconfirmed; no delivery confirmation exists."},
        ])
        self.assertTrue(any(c["type"] == "proposal_receipt_conflict" for c in conflicts))

    def test_nested_hindsight_context_is_not_shown_as_memory_content(self):
        prospect_id = normalize_prospect_id("Rahul Sharma", "Acme Technologies")
        memories = clean_and_deduplicate_memories([
            {"text": "Rahul received the revised proposal. (2026-11-06 00:00: Rahul requested a revised proposal.)", "tags": [f"prospect:{prospect_id}"]}
        ], prospect_id, "Rahul Sharma", "Acme Technologies")
        self.assertEqual(memories[0]["text"], "Rahul received the revised proposal.")

    def test_receipt_conflict_overrides_send_recommendation(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = DatabaseManager(path)
            hindsight = MagicMock()
            hindsight.is_connected = False
            hindsight.search_memories.return_value = (
                [
                    "Rahul received the revised proposal.",
                    "Proposal delivery unconfirmed; no delivery confirmation exists.",
                ], 2, 2,
            )
            ai = MagicMock()
            ai.analyze_and_reason.return_value = {
                "customer_state": {"team_promises": ["Send revised proposal", "Send revised proposal"], "unresolved_actions": ["Send revised proposal"]},
                "next_best_action": {"recommended_action": "Send revised proposal", "timing": "Today"},
                "proposed_task_tool": {"description": "Send revised proposal", "responsible_person": "Sales Team"},
            }
            ai.generate_meeting_brief.return_value = "Brief"
            result = RecallAgent(hindsight_service=hindsight, ai_service=ai, db_manager=db).process_meeting(
                name="Rahul Sharma", company="Acme Technologies", goal="Review proposal", notes="Please confirm the proposal status."
            )
            self.assertIn("Verify", result["next_best_action"]["recommended_action"])
            self.assertIn("Verify", result["proposed_task"]["description"])
            self.assertEqual(result["customer_state"]["team_promises"], ["Send revised proposal"])
            self.assertEqual(result["customer_state"]["unresolved_actions"], [])
        finally:
            os.remove(path)

    def test_shared_first_name_cannot_pass_prospect_isolation(self):
        target_id = normalize_prospect_id("Arjun Mehta", "Nexora Technologies")
        memories = clean_and_deduplicate_memories([
            {"text": "Arjun Nair requested a proposal for NovaTech Systems.", "tags": []},
            {"text": "Arjun Mehta requested a proposal for Nexora Technologies.", "tags": []},
        ], target_id, "Arjun Mehta", "Nexora Technologies")
        self.assertEqual([memory["text"] for memory in memories], ["Arjun Mehta requested a proposal for Nexora Technologies."])

    def test_hindsight_never_falls_back_to_untagged_recall(self):
        client = MagicMock()
        client.recall.return_value = MagicMock(results=[])
        service = HindsightService(client=client)
        memories, unique_count, raw_count = service.search_memories(
            query="history", prospect_name="Arjun Mehta", company="Nexora Technologies"
        )
        self.assertEqual((memories, unique_count, raw_count), ([], 0, 0))
        client.recall.assert_called_once()
        self.assertEqual(client.recall.call_args.kwargs["tags_match"], "all_strict")

    def test_completed_and_cancelled_tasks_are_terminal(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            tasks = TaskService(db_manager=DatabaseManager(path))
            proposal = tasks.propose_task("p", "Prospect", "Company", "Follow up", "Reason", "Evidence")
            completed = tasks.approve_task(proposal["proposal_id"])
            tasks.update_outcome(completed["task_id"], "Completed", "Done")
            with self.assertRaises(ValueError):
                tasks.update_outcome(completed["task_id"], "In Progress", "Reopen")

            proposal = tasks.propose_task("p", "Prospect", "Company", "Second follow up", "Reason", "Evidence")
            cancelled = tasks.approve_task(proposal["proposal_id"])
            tasks.update_outcome(cancelled["task_id"], "Cancelled", "No longer needed")
            with self.assertRaises(ValueError):
                tasks.update_outcome(cancelled["task_id"], "Pending", "Reopen")
        finally:
            os.remove(path)

    def test_explicit_friday_date_beats_bare_weekday_ambiguity(self):
        result = resolve_relative_deadline("by Friday Nov 13, 2026")
        self.assertFalse(result["is_ambiguous"])
        self.assertEqual(result["resolved_date"], "2026-11-13")

    def test_fallback_does_not_mark_initial_quote_review_as_revised_proposal_review(self):
        analysis = AIService(offline_mode=True)._build_fallback_reasoning(
            "Arjun Mehta", "Nexora Technologies", "Pricing discussion",
            "Arjun said his CFO reviewed the initial quote and requested a revised quotation.", [],
        )
        self.assertEqual(analysis["customer_state"]["document_lifecycle"][0]["state"], "Requested")

    def test_pending_task_rejects_placeholder_outcomes(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            tasks = TaskService(db_manager=DatabaseManager(path))
            proposal = tasks.propose_task("p", "Prospect", "Company", "Follow up", "Reason", "Evidence")
            task = tasks.approve_task(proposal["proposal_id"])
            with self.assertRaises(ValueError):
                tasks.update_outcome(task["task_id"], "Pending", "nope")
            with self.assertRaises(ValueError):
                tasks.update_outcome(task["task_id"], "Completed", "")
        finally:
            os.remove(path)

    def test_explicit_note_identity_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            RecallAgent._validate_note_identity(
                "Arjun Mehta", "Rahul mentioned the CFO reviewed the proposal."
            )

    def test_decision_stage_sanitizer_rejects_interest_as_purchase_decision(self):
        stages = RecallAgent._sanitize_decision_stages(
            [{"type": "Purchase Intent", "status": "Confirmed"}, {"type": "Final Purchase Approval", "status": "Confirmed"}],
            "Arjun expressed interest, but no purchase approval was granted and no contract was signed."
        )
        self.assertEqual(stages, [])

    def test_lifecycle_does_not_mark_technical_requirements_as_proposal_approved(self):
        agent = RecallAgent(hindsight_service=HindsightService(offline_mode=True), ai_service=AIService(offline_mode=True))
        lifecycle = agent._sanitize_document_lifecycle([
            {"document": "Compliance Proposal", "state": "Requested", "responsible": "Priya"},
            {"document": "Compliance Proposal", "state": "Reviewed", "responsible": "Priya"},
            {"document": "Compliance Proposal", "state": "Approved", "responsible": "Priya"},
        ], "Priya's security team reviewed the compliance proposal and approved the technical requirements. No purchase approval occurred.")
        self.assertEqual(len(lifecycle), 1)
        self.assertEqual(lifecycle[0]["state"], "Reviewed")

    def test_received_proposal_advances_next_action_to_final_authorization(self):
        agent = RecallAgent(hindsight_service=HindsightService(offline_mode=True), ai_service=AIService(offline_mode=True))
        state = {
            "completed_actions": [],
            "unresolved_actions": ["Verify delivery and receipt of proposal"],
        }
        recommendation = {"recommended_action": "Verify whether the proposal was delivered"}
        agent._reconcile_verified_actions(
            "Daniel Foster",
            "Daniel Foster confirmed that he received the revised proposal. His CFO will join the final purchase discussion. No purchase approval or contract has been signed.",
            ["Daniel Foster received the revised proposal."],
            state,
            recommendation,
        )
        self.assertIn("final purchase authorization", recommendation["recommended_action"])
        self.assertIn("Proposal receipt confirmed", state["completed_actions"])
        self.assertEqual(state["unresolved_actions"], [])

    def test_requested_meeting_is_not_marked_scheduled(self):
        analysis = AIService(offline_mode=True)._build_fallback_reasoning(
            "Daniel Foster", "Nexora Technologies", "Plan next steps",
            "Daniel requested a meeting with his CFO and the sales team. The meeting has not been scheduled.", [],
        )
        self.assertEqual(analysis["customer_state"]["scheduled_actions"], [])

    def test_confirmed_meeting_is_marked_scheduled(self):
        analysis = AIService(offline_mode=True)._build_fallback_reasoning(
            "Daniel Foster", "Nexora Technologies", "Plan next steps",
            "Daniel confirmed a meeting with his CFO and the sales team for next Tuesday.", [],
        )
        self.assertTrue(analysis["customer_state"]["scheduled_actions"])


if __name__ == "__main__":
    unittest.main()
