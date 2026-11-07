"""
Recall — Intelligence Accuracy, Agent Reliability & Workflow Audit Test Suite
Covers all 22 required audit specifications:
1. Conditional approval versus final purchase approval
2. Purchase intent versus signed contract
3. Proposal promised but not sent
4. Proposal sent but receipt unconfirmed
5. Proposal received but not reviewed
6. Similar memories with different dates (distinct events)
7. Duplicate memories with different wording (semantic deduplication)
8. Conflicting customer decisions
9. Correct requester and task-owner identification
10. Latest confirmed event overriding outdated state
11. Completed action not recommended again
12. Rejected proposal not recreated unnecessarily
13. Repeated approval request creating exactly one task
14. Rejected proposal never executing
15. Same prospect retaining a stable identity
16. Different prospects remaining isolated
17. Expired deadlines not being treated as completed
18. Completed task not returning to Pending
19. SQLite success with Hindsight failure & retry sync
20. Groq and Hindsight unavailable (resilient offline mode)
21. Invalid model output handling
22. Outcome successfully retained and recalled in a future briefing
"""

import os
import unittest
import tempfile
import json
from datetime import datetime, date, timedelta, timezone
from unittest.mock import MagicMock, patch

from database import DatabaseManager, init_db
from memory_processor import (
    normalize_prospect_id,
    clean_and_deduplicate_memories,
    detect_memory_conflicts
)
from hindsight_service import HindsightService
from ai_service import AIService, extract_json_from_text
from task_service import TaskService
from agent_service import RecallAgent
from date_validator import (
    validate_weekday_date_combination,
    detect_deadline_conflicts,
    resolve_relative_deadline,
    sanitize_deadline,
    extract_deadline_phrase,
    APP_TIMEZONE
)


class TestRecallAccuracyAudit(unittest.TestCase):

    def setUp(self):
        # Create an isolated temporary SQLite database for each test
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        init_db(self.temp_db_path)
        self.db = DatabaseManager(self.temp_db_path)

        # Mock Hindsight Client
        self.mock_hindsight_client = MagicMock()
        self.hindsight_service = HindsightService(client=self.mock_hindsight_client)

        # Mock Groq Client
        self.mock_groq_client = MagicMock()
        self.ai_service = AIService(client=self.mock_groq_client)

        # Initialize TaskService and Agent
        self.task_service = TaskService(db_manager=self.db, hindsight_service=self.hindsight_service)
        self.agent = RecallAgent(
            hindsight_service=self.hindsight_service,
            ai_service=self.ai_service,
            db_manager=self.db
        )

    def tearDown(self):
        os.close(self.temp_db_fd)
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    # 1. Conditional approval versus final purchase approval
    def test_01_conditional_approval_vs_final_purchase_approval(self):
        notes = "CFO gave conditional approval for the enterprise tier provided we deliver by Dec 1 with 24/7 custom SLA."
        analysis = self.ai_service._build_fallback_reasoning(
            prospect_name="Rahul Sharma",
            company="Acme Technologies",
            meeting_goal="Review terms",
            latest_notes=notes,
            memories=[]
        )
        dec_stages = analysis["customer_state"]["decision_stages"]
        self.assertTrue(any(d["type"] == "Conditional Approval" for d in dec_stages))
        self.assertFalse(any(d["type"] == "Final Purchase Approval" for d in dec_stages))
        self.assertFalse(any(d["type"] == "Contract Signed" for d in dec_stages))

    # 2. Purchase intent versus signed contract
    def test_02_purchase_intent_vs_signed_contract(self):
        notes = "Customer expressed strong purchase intent and wants to move forward, but contract remains unsigned."
        analysis = self.ai_service._build_fallback_reasoning(
            prospect_name="Sarah Connor",
            company="Cyberdyne",
            meeting_goal="Contract discussion",
            latest_notes=notes,
            memories=[]
        )
        dec_stages = analysis["customer_state"]["decision_stages"]
        self.assertTrue(any(d["type"] == "Purchase Intent" for d in dec_stages))
        self.assertFalse(any(d["type"] == "Contract Signed" for d in dec_stages))
        self.assertFalse(any(d["type"] == "Purchase Completed" for d in dec_stages))

    # 3. Proposal promised but not sent
    def test_03_proposal_promised_but_not_sent(self):
        notes = "Sales team promised to deliver a customized proposal by Friday. Delivery is unconfirmed."
        analysis = self.ai_service._build_fallback_reasoning(
            prospect_name="Vikram Patel",
            company="Globex",
            meeting_goal="Proposal follow-up",
            latest_notes=notes,
            memories=[]
        )
        doc_lifecycle = analysis["customer_state"]["document_lifecycle"]
        prop_doc = next((d for d in doc_lifecycle if "Proposal" in d["document"]), None)
        self.assertIsNotNone(prop_doc)
        self.assertEqual(prop_doc["state"], "Promised")
        self.assertNotEqual(prop_doc["state"], "Sent")
        self.assertNotEqual(prop_doc["state"], "Delivered")
        # Recommendation must verify delivery, not assume sent
        self.assertIn("Verify whether the proposal", analysis["next_best_action"]["recommended_action"])

    # 4. Proposal sent but receipt unconfirmed
    def test_04_proposal_sent_but_receipt_unconfirmed(self):
        notes = "Proposal was sent yesterday via email. Awaiting confirmation of receipt from prospect."
        analysis = self.ai_service._build_fallback_reasoning(
            prospect_name="Meera Reddy",
            company="Zenith",
            meeting_goal="Check receipt",
            latest_notes=notes,
            memories=[]
        )
        # Should not infer received or reviewed
        rec_action = analysis["next_best_action"]["recommended_action"]
        self.assertIn("delivered to and received by", rec_action)

    # 5. Proposal received but not reviewed
    def test_05_proposal_received_but_not_reviewed(self):
        notes = "Proposal was delivered to Vikram and receipt was confirmed. Customer review is pending."
        memories = ["Proposal delivered to Vikram on Nov 04."]
        analysis = self.ai_service._build_fallback_reasoning(
            prospect_name="Vikram Patel",
            company="Globex",
            meeting_goal="Review status",
            latest_notes=notes,
            memories=memories
        )
        rec_action = analysis["next_best_action"]["recommended_action"]
        self.assertIn("reviewed the delivered proposal", rec_action)

    # 6. Similar memories with different dates (distinct events preserved)
    def test_06_similar_memories_with_different_dates(self):
        p_id = normalize_prospect_id("Rahul Sharma", "Acme")
        raw = [
            {"id": "1", "text": "Prospect: Rahul Sharma at Acme. Meeting on Oct 12: Sales team promised demo."},
            {"id": "2", "text": "Prospect: Rahul Sharma at Acme. Meeting on Nov 02: Sales team promised demo."}
        ]
        cleaned = clean_and_deduplicate_memories(raw, p_id, "Rahul Sharma", "Acme")
        # Distinct dates MUST BOTH be preserved!
        self.assertEqual(len(cleaned), 2)

    # 7. Duplicate memories with different wording (semantic deduplication)
    def test_07_duplicate_memories_with_different_wording(self):
        p_id = normalize_prospect_id("Rahul Sharma", "Acme")
        raw = [
            {"id": "1", "text": "Prospect: Rahul Sharma at Acme. Customer requested a pricing proposal on Friday."},
            {"id": "2", "text": "Prospect: Rahul Sharma at Acme. Customer asked for proposal on pricing for Friday."}
        ]
        cleaned = clean_and_deduplicate_memories(raw, p_id, "Rahul Sharma", "Acme")
        # Same event/date with minor rephrasing should deduplicate to 1
        self.assertEqual(len(cleaned), 1)

    # 8. Conflicting customer decisions
    def test_08_conflicting_customer_decisions(self):
        memories = [
            {"text": "Initial call Oct 10: CFO expressed strong pricing objection and rejected quote."},
            {"text": "Call Nov 05: Revised enterprise pricing approved by CFO."}
        ]
        conflicts = detect_memory_conflicts(memories)
        self.assertGreater(len(conflicts), 0)
        self.assertEqual(conflicts[0]["type"], "pricing_status_conflict")
        self.assertIn("latest confirmed decision takes precedence", conflicts[0]["description"])

    # 9. Correct requester and task-owner identification
    def test_09_correct_requester_and_task_owner_identification(self):
        proposal = self.task_service.propose_task(
            prospect_id="acme_rahul",
            prospect_name="Rahul Sharma",
            company="Acme",
            description="Prepare revised enterprise quote",
            reason="Customer requested custom SLA terms",
            supporting_evidence="Notes from Oct 12",
            requester="Rahul Sharma",
            responsible_person="Sales Team"
        )
        self.assertEqual(proposal["requester"], "Rahul Sharma")
        self.assertEqual(proposal["responsible_person"], "Sales Team")
        self.assertNotEqual(proposal["responsible_person"], "Rahul Sharma")

    # 10. Latest confirmed event overriding outdated state
    def test_10_latest_confirmed_event_overriding_outdated_state(self):
        p_id = normalize_prospect_id("David", "Apex")
        state_early = {
            "prospect_id": p_id,
            "current_stage": "Discovery",
            "completed_actions": [],
            "unresolved_actions": ["Deliver product demonstration"]
        }
        self.db.save_customer_state(state_early)

        state_later = {
            "prospect_id": p_id,
            "current_stage": "Demonstration Completed",
            "completed_actions": ["Deliver product demonstration"],
            "unresolved_actions": ["Send commercial proposal"]
        }
        self.db.save_customer_state(state_later)

        recovered = self.db.get_customer_state(p_id)
        self.assertEqual(recovered["current_stage"], "Demonstration Completed")
        self.assertIn("Deliver product demonstration", recovered["completed_actions"])

    # 11. Completed action not recommended again
    def test_11_completed_action_not_recommended_again(self):
        memories = [
            "CONFIRMED TASK OUTCOME: Product demonstration completed on Nov 02.",
            "CONFIRMED TASK OUTCOME: Customized pricing proposal delivered to Arjun."
        ]
        analysis = self.ai_service._build_fallback_reasoning(
            prospect_name="Arjun Nair",
            company="Apex Labs",
            meeting_goal="Next steps",
            latest_notes="Discuss next steps",
            memories=memories
        )
        rec = analysis["next_best_action"]["recommended_action"]
        self.assertNotIn("Schedule demonstration", rec)
        self.assertNotIn("Verify whether the proposal or pricing details have been delivered", rec)

    # 12. Rejected proposal not recreated unnecessarily
    def test_12_rejected_proposal_not_recreated_unnecessarily(self):
        p_id = "test_prospect_rej"
        prop = self.task_service.propose_task(
            prospect_id=p_id,
            prospect_name="John Doe",
            company="Acme",
            description="Send generic product brochure",
            reason="Lead qualification",
            supporting_evidence="Meeting notes Oct 01"
        )
        prop_id = prop["proposal_id"]
        self.task_service.reject_task(prop_id, reason="Customer already has brochure")

        # Attempt to propose identical task with same evidence
        recreated = self.task_service.propose_task(
            prospect_id=p_id,
            prospect_name="John Doe",
            company="Acme",
            description="Send generic product brochure",
            reason="Lead qualification",
            supporting_evidence="Meeting notes Oct 01"
        )
        # Must return None to prevent silent recreation
        self.assertIsNone(recreated)

    # 13. Repeated approval request creating exactly one task
    def test_13_repeated_approval_request_creating_exactly_one_task(self):
        p_id = "test_prospect_app"
        prop = self.task_service.propose_task(
            prospect_id=p_id,
            prospect_name="Alice",
            company="Wonderland",
            description="Setup sandbox environment",
            reason="Customer testing",
            supporting_evidence="Call notes"
        )
        prop_id = prop["proposal_id"]
        task1 = self.task_service.approve_task(prop_id)
        self.assertIsNotNone(task1)

        # Second approval must raise ValueError
        with self.assertRaises(ValueError):
            self.task_service.approve_task(prop_id)

        tasks = self.db.list_tasks(p_id)
        self.assertEqual(len(tasks), 1)

    # 14. Rejected proposal never executing
    def test_14_rejected_proposal_never_executing(self):
        p_id = "test_prospect_rej2"
        prop = self.task_service.propose_task(
            prospect_id=p_id,
            prospect_name="Bob",
            company="Builder Co",
            description="Send unsolicited quote",
            reason="Proactive outreach",
            supporting_evidence="None"
        )
        prop_id = prop["proposal_id"]
        self.task_service.reject_task(prop_id, reason="Premature action")

        # Attempting to approve a rejected proposal must raise ValueError
        with self.assertRaises(ValueError):
            self.task_service.approve_task(prop_id)

        tasks = self.db.list_tasks(p_id)
        self.assertEqual(len(tasks), 0)

    # 15. Same prospect retaining a stable identity
    def test_15_same_prospect_retaining_stable_identity(self):
        id1 = normalize_prospect_id("Rahul Sharma", "Acme Technologies, Inc.")
        id2 = normalize_prospect_id("rahul sharma", "Acme Technologies")
        id3 = normalize_prospect_id("  Rahul  Sharma  ", "Acme Technologies LLC")
        self.assertEqual(id1, id2)
        self.assertEqual(id2, id3)
        self.assertEqual(id1, "acme-technologies_rahul-sharma")

    # 16. Different prospects remaining isolated
    def test_16_different_prospects_remaining_isolated(self):
        id_rahul = normalize_prospect_id("Rahul Sharma", "Acme")
        id_rohan = normalize_prospect_id("Rohan Sharma", "Acme")
        id_globex = normalize_prospect_id("Rahul Sharma", "Globex")
        self.assertNotEqual(id_rahul, id_rohan)
        self.assertNotEqual(id_rahul, id_globex)

    # 17. Expired deadlines not being treated as completed
    def test_17_expired_deadlines_not_treated_as_completed(self):
        notes = "Sales team promised to send customized proposal by Oct 01, 2026. Current date is Nov 06, 2026. Delivery unconfirmed."
        analysis = self.ai_service._build_fallback_reasoning(
            prospect_name="Arjun Nair",
            company="Apex",
            meeting_goal="Status check",
            latest_notes=notes,
            memories=[]
        )
        completed = analysis["customer_state"]["completed_actions"]
        self.assertNotIn("Send customized proposal", completed)
        self.assertIn("Status not confirmed", analysis["next_best_action"]["current_status"])

    # 18. Completed task not returning to Pending
    def test_18_completed_task_not_returning_to_pending(self):
        prop = self.task_service.propose_task(
            prospect_id="p_test_trans",
            prospect_name="Test User",
            company="Test Corp",
            description="Execute customer demo",
            reason="Evaluation",
            supporting_evidence="Notes"
        )
        task = self.task_service.approve_task(prop["proposal_id"])
        t_id = task["task_id"]

        # Mark Completed
        self.task_service.update_outcome(t_id, "Completed", "Demo finished successfully.")

        # Attempt to silently revert Completed -> Pending must raise ValueError
        with self.assertRaises(ValueError):
            self.task_service.update_outcome(t_id, "Pending", "Reverting to pending")

    # 19. SQLite success with Hindsight failure & retry sync
    def test_19_sqlite_success_with_hindsight_failure_and_retry(self):
        # Configure mock Hindsight to fail on first retain call
        failing_hindsight = MagicMock()
        failing_hindsight.save_confirmed_outcome.side_effect = RuntimeError("Hindsight Cloud network timeout")
        failing_hindsight.is_connected = True

        task_service = TaskService(db_manager=self.db, hindsight_service=failing_hindsight)

        prop = task_service.propose_task(
            prospect_id="p_sync_test",
            prospect_name="Priya Sharma",
            company="ZetaCorp",
            description="Verify compliance questionnaire",
            reason="Security review",
            supporting_evidence="Notes Nov 01"
        )
        task = task_service.approve_task(prop["proposal_id"])
        t_id = task["task_id"]

        # Update outcome during Hindsight failure
        res = task_service.update_outcome(t_id, "Completed", "Questionnaire verified and approved.")
        self.assertTrue(res["success"])
        self.assertFalse(res["hindsight_synced"])
        self.assertEqual(res["hindsight_sync_status"], "failed")

        # Verify task is preserved in SQLite
        db_task = self.db.get_task(t_id)
        self.assertEqual(db_task["task_status"], "Completed")
        self.assertEqual(db_task["hindsight_sync_status"], "failed")

        # Now simulate Hindsight recovering and retry sync
        failing_hindsight.save_confirmed_outcome.side_effect = None
        failing_hindsight.save_confirmed_outcome.return_value = MagicMock(id="mem_recovered_123")

        retry_res = task_service.retry_hindsight_sync(t_id)
        self.assertTrue(retry_res["success"])
        updated_db_task = self.db.get_task(t_id)
        self.assertEqual(updated_db_task["hindsight_sync_status"], "synced")
        self.assertEqual(updated_db_task["hindsight_memory_id"], "mem_recovered_123")

    # 20. Groq and Hindsight unavailable (resilient offline mode)
    def test_20_groq_and_hindsight_unavailable(self):
        offline_hindsight = HindsightService(offline_mode=True)
        offline_ai = AIService(offline_mode=True)
        offline_agent = RecallAgent(
            hindsight_service=offline_hindsight,
            ai_service=offline_ai,
            db_manager=self.db
        )

        res = offline_agent.process_meeting(
            name="Emergency Contact",
            company="Backup Corp",
            goal="Discuss offline continuity plan",
            notes="Systems are operating in standalone mode."
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["ai_reasoning_mode"], "fallback_deterministic")
        self.assertIn("Backup Corp", res["brief"])

    # 21. Invalid model output handling
    def test_21_invalid_model_output(self):
        invalid_outputs = [
            "This is just conversational text without any JSON.",
            "```json\n{ truncated_json: true, missing_close_brace: \n",
            ""
        ]
        for bad_text in invalid_outputs:
            parsed = extract_json_from_text(bad_text)
            self.assertIsNone(parsed)

        # Verify analyze_and_reason handles broken model output without crashing
        self.mock_groq_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Malformed text"))]
        )
        analysis = self.ai_service.analyze_and_reason(
            prospect_name="Rahul",
            company="Acme",
            meeting_goal="Demo",
            latest_notes="Notes",
            memories=[]
        )
        self.assertEqual(analysis["ai_reasoning_mode"], "fallback_deterministic")
        self.assertIn("customer_state", analysis)

    # 22. Outcome successfully retained and recalled in a future briefing
    def test_22_outcome_retained_and_recalled_in_future_briefing(self):
        p_id = normalize_prospect_id("Rahul Sharma", "Acme")

        # Step 1: User completes task and records outcome
        prop = self.task_service.propose_task(
            prospect_id=p_id,
            prospect_name="Rahul Sharma",
            company="Acme",
            description="Verify proposal receipt",
            reason="Promised delivery",
            supporting_evidence="Oct notes"
        )
        task = self.task_service.approve_task(prop["proposal_id"])
        self.task_service.update_outcome(
            task_id=task["task_id"],
            new_status="Completed",
            outcome_note="Proposal confirmed delivered on Nov 04 and received by CFO."
        )

        # Verify Hindsight retain was called with prospect tag
        self.mock_hindsight_client.retain.assert_called()
        retain_call = self.mock_hindsight_client.retain.call_args[1]
        self.assertIn(f"prospect:{p_id}", retain_call["tags"])
        self.assertIn("CONFIRMED TASK OUTCOME", retain_call["content"])

        # Step 2: In a future briefing, Hindsight returns this confirmed outcome
        confirmed_memory = (
            "CONFIRMED TASK OUTCOME (2026-11-04): Prospect: Rahul Sharma (Acme). "
            "Action/Task Completed: Verify proposal receipt. "
            "Confirmed Outcome Note: Proposal confirmed delivered on Nov 04 and received by CFO."
        )
        self.mock_hindsight_client.recall.return_value = MagicMock(
            results=[MagicMock(text=confirmed_memory, tags=[f"prospect:{p_id}"])]
        )

        memories, unique_count, raw_count = self.hindsight_service.search_memories(
            query="Status of proposal",
            prospect_name="Rahul Sharma",
            company="Acme",
            prospect_id=p_id
        )
        self.assertEqual(unique_count, 1)
        self.assertIn("CONFIRMED TASK OUTCOME", memories[0])


class TestReviewAndReasoningAccuracy(unittest.TestCase):
    """
    Focused tests for the 6 specific review & reasoning criteria:
    1. Incorrect weekday/date combinations
    2. Conflicting deadlines & ambiguous deadline human confirmation
    3. Conditional versus final purchase approval
    4. Requester versus responsible-party attribution
    5. Deduplication and distinct approval memories (finance manager vs CFO)
    6. Prospect isolation with similar company names
    """

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        init_db(self.temp_db_path)
        self.db = DatabaseManager(self.temp_db_path)
        self.mock_hindsight = MagicMock()
        self.hindsight_service = HindsightService(client=self.mock_hindsight)
        self.mock_groq = MagicMock()
        self.ai_service = AIService(client=self.mock_groq)
        self.task_service = TaskService(db_manager=self.db, hindsight_service=self.hindsight_service)
        self.agent = RecallAgent(
            hindsight_service=self.hindsight_service,
            ai_service=self.ai_service,
            db_manager=self.db
        )

    def tearDown(self):
        os.close(self.temp_db_fd)
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    # 1. Date and deadline validation: Incorrect weekday/date combinations
    def test_incorrect_weekday_date_combination(self):
        # User specified "Friday, 2026-11-10". Nov 10, 2026 is Tuesday!
        res = validate_weekday_date_combination("We scheduled delivery for Friday, 2026-11-10.")
        self.assertFalse(res["is_valid"])
        self.assertEqual(res["weekday_mentioned"], "Friday")
        self.assertEqual(res["calendar_date"], "2026-11-10")
        self.assertEqual(res["actual_weekday"], "Tuesday")
        self.assertIn("Tuesday", res["error_message"])
        self.assertIn("not a Friday", res["error_message"])

        # Valid combination: Friday, 2026-11-06 (Nov 06, 2026 is Friday)
        res_valid = validate_weekday_date_combination("We scheduled delivery for Friday, 2026-11-06.")
        self.assertTrue(res_valid["is_valid"])
        self.assertIsNone(res_valid["error_message"])

    # 2. Conflicting deadlines: Historical Nov 13 vs latest "by Friday"
    def test_conflicting_deadlines_detection(self):
        historical_memories = [
            "Rahul requested a revised proposal by November 13, 2026."
        ]
        latest_notes = "CFO conditional approval requires delivery by Friday."
        conflict = detect_deadline_conflicts(historical_memories, latest_notes)
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["type"], "conflicting_deadlines")
        self.assertIn("2026-11-13", conflict["description"])
        self.assertTrue(conflict["requires_human_confirmation"])

    # 3. Conflicting deadline lowers confidence and blocks task creation until confirmed
    def test_deadline_conflict_lowers_confidence_and_blocks_task_creation(self):
        historical = ["Rahul requested a revised proposal by November 13, 2026."]
        self.mock_hindsight.recall.return_value = MagicMock(
            results=[MagicMock(text=historical[0], tags=["prospect:acme-technologies_rahul-sharma"])]
        )
        self.mock_groq.chat.completions.create.side_effect = Exception("Offline test")

        res = self.agent.process_meeting(
            name="Rahul Sharma",
            company="Acme Technologies",
            goal="Finalize proposal deadline",
            notes="CFO conditional approval requires delivery by Friday."
        )

        # 1. Recommendation confidence lowered
        nba = res["next_best_action"]
        self.assertEqual(nba["confidence"], "Low (Unresolved deadline conflict)")
        self.assertIn("unresolved_uncertainty", nba)

        # 2. Proposal flagged with deadline conflict
        prop = res["proposed_task"]
        self.assertIsNotNone(prop)
        self.assertTrue(prop["has_deadline_conflict"])

        # 3. Unresolved deadline conflict BLOCKS task creation without confirmation
        with self.assertRaises(ValueError) as ctx:
            self.task_service.approve_task(prop["proposal_id"], confirmed_due_date=None)
        self.assertIn("Unresolved deadline conflict", str(ctx.exception))

        # 4. Approving with explicit confirmed_due_date succeeds
        task = self.task_service.approve_task(prop["proposal_id"], confirmed_due_date="2026-11-13")
        self.assertIsNotNone(task)
        self.assertEqual(task["confirmed_due_date"], "2026-11-13")
        self.assertEqual(task["deadline_source"], "Human confirmed via approval gate")

        # 5. Database stores confirmed date separately from natural language deadline
        db_task = self.db.get_task(task["task_id"])
        self.assertEqual(db_task["confirmed_due_date"], "2026-11-13")
        self.assertIsNotNone(db_task["natural_language_deadline"])

    # 4. Conditional versus final purchase approval
    def test_conditional_vs_final_approval(self):
        cond_notes = "CFO gave conditional approval for revised pricing provided 24/7 SLA is delivered by Dec 1."
        cond_res = self.ai_service._build_fallback_reasoning(
            prospect_name="Rahul Sharma",
            company="Acme Technologies",
            meeting_goal="Review terms",
            latest_notes=cond_notes,
            memories=[]
        )
        stages = cond_res["customer_state"]["decision_stages"]
        cond_stage = next((s for s in stages if s["type"] == "Conditional Approval"), None)
        self.assertIsNotNone(cond_stage)
        self.assertIn("24/7 SLA", cond_stage["conditions"])
        self.assertFalse(any(s["type"] == "Final Purchase Approval" for s in stages))
        self.assertFalse(any(s["type"] == "Contract Signed" for s in stages))

        # Final purchase approval
        final_notes = "Finance committee granted final purchase approval for the enterprise agreement."
        final_res = self.ai_service._build_fallback_reasoning(
            prospect_name="Rahul Sharma",
            company="Acme Technologies",
            meeting_goal="Review terms",
            latest_notes=final_notes,
            memories=[]
        )
        final_stages = final_res["customer_state"]["decision_stages"]
        self.assertTrue(any(s["type"] == "Final Purchase Approval" for s in final_stages))
        self.assertFalse(any(s["type"] == "Conditional Approval" for s in final_stages))

    # 5. Responsibility attribution: requester vs commitment maker vs responsible party
    def test_responsibility_attribution_sales_commitment(self):
        notes = "Sales team agreed to deliver the revised pricing proposal by Friday. Rahul Sharma will review."
        res = self.agent.process_meeting(
            name="Rahul Sharma",
            company="Acme Technologies",
            goal="Proposal delivery",
            notes=notes
        )
        prop = res["proposed_task"]
        self.assertIsNotNone(prop)
        # Sales team is responsible party and commitment maker, Rahul is requester/reviewer
        self.assertEqual(prop["responsible_person"], "Sales Team")
        self.assertEqual(prop["commitment_maker"], "Sales Team")
        self.assertNotEqual(prop["responsible_person"], "Rahul Sharma")
        self.assertIsNotNone(prop["responsibility_source"])

    # 6. Deduplication of finance-manager and CFO approval on same date
    def test_duplicate_finance_approval_memories(self):
        p_id = normalize_prospect_id("Rahul Sharma", "Acme")
        raw = [
            {"id": "1", "text": "Prospect: Rahul Sharma at Acme. Finance manager approved revised pricing proposal on Nov 05."},
            {"id": "2", "text": "Prospect: Rahul Sharma at Acme. CFO gave approval for revised pricing proposal on Nov 05."}
        ]
        cleaned = clean_and_deduplicate_memories(raw, p_id, "Rahul Sharma", "Acme")
        # Semantically duplicate approval events on the SAME date should deduplicate to 1
        self.assertEqual(len(cleaned), 1)

    # 7. Preservation of distinct approval memories on different dates or conditions
    def test_distinct_approval_memories_preserved(self):
        p_id = normalize_prospect_id("Rahul Sharma", "Acme")
        
        # Different dates -> preserved
        raw_diff_dates = [
            {"id": "1", "text": "Prospect: Rahul Sharma at Acme. Finance manager approved revised pricing proposal on Nov 05."},
            {"id": "2", "text": "Prospect: Rahul Sharma at Acme. CFO approved revised pricing proposal on Nov 12."}
        ]
        cleaned_dates = clean_and_deduplicate_memories(raw_diff_dates, p_id, "Rahul Sharma", "Acme")
        self.assertEqual(len(cleaned_dates), 2)

        # Different conditions -> preserved
        raw_diff_conds = [
            {"id": "3", "text": "Prospect: Rahul Sharma at Acme. CFO approved pricing provided delivery is by Dec 1 on Nov 05."},
            {"id": "4", "text": "Prospect: Rahul Sharma at Acme. CFO approved pricing with no special condition on Nov 05."}
        ]
        cleaned_conds = clean_and_deduplicate_memories(raw_diff_conds, p_id, "Rahul Sharma", "Acme")
        self.assertEqual(len(cleaned_conds), 2)

    # 8. Strict prospect isolation with similar companies
    def test_strict_prospect_isolation_similar_companies(self):
        p_id_tech = normalize_prospect_id("Rahul Sharma", "Acme Technologies")
        p_id_glob = normalize_prospect_id("Rahul Sharma", "Acme Global")
        self.assertNotEqual(p_id_tech, p_id_glob)

        raw = [
            {"id": "1", "text": "Customer Rahul Sharma at Company: Acme Technologies approved enterprise pilot.", "tags": [f"prospect:{p_id_tech}"]},
            {"id": "2", "text": "Customer Rahul Sharma at Company: Acme Global rejected enterprise pilot.", "tags": [f"prospect:{p_id_glob}"]}
        ]
        cleaned_tech = clean_and_deduplicate_memories(raw, p_id_tech, "Rahul Sharma", "Acme Technologies")
        self.assertEqual(len(cleaned_tech), 1)
        self.assertIn("Acme Technologies", cleaned_tech[0]["text"])
        self.assertNotIn("Acme Global", cleaned_tech[0]["text"])


class TestLiveDashboardAuditRemediation(unittest.TestCase):
    """
    Acceptance tests for the 6 critical remediation areas:
    1. November 10, 2026 being rejected as Friday.
    2. November 6 and November 13 remaining separate interpretations of Friday.
    3. An unresolved conflict preventing task creation.
    4. No unconfirmed date propagating into a task.
    5. Existing historical tasks being preserved.
    6. Completed internal tasks not automatically completing document delivery.
    7. Semantic duplicates being removed from displayed memories.
    8. Distinct approval events remaining separate.
    9. Hindsight sync retry working without duplicate records.
    """

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        init_db(self.temp_db_path)
        self.db = DatabaseManager(self.temp_db_path)
        self.mock_hindsight = MagicMock()
        self.hindsight_service = HindsightService(client=self.mock_hindsight)
        self.mock_groq = MagicMock()
        self.ai_service = AIService(client=self.mock_groq)
        self.task_service = TaskService(db_manager=self.db, hindsight_service=self.hindsight_service)
        self.agent = RecallAgent(
            hindsight_service=self.hindsight_service,
            ai_service=self.ai_service,
            db_manager=self.db
        )

    def tearDown(self):
        os.close(self.temp_db_fd)
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    # 1. November 10, 2026 being rejected as Friday
    def test_01_nov_10_rejected_as_friday(self):
        val = validate_weekday_date_combination("Proposal required by Friday, 2026-11-10.")
        self.assertFalse(val["is_valid"])
        self.assertEqual(val["actual_weekday"], "Tuesday")
        self.assertEqual(val["weekday_mentioned"], "Friday")
        self.assertIn("Tuesday", val["error_message"])

        # Sanitize deadline rejects 2026-11-10 when Friday is context
        sanitized = sanitize_deadline("2026-11-10", notes_or_text="delivery by Friday")
        self.assertIsNone(sanitized)

    # 2. November 6 and November 13 remaining separate interpretations of Friday
    def test_02_nov_06_and_nov_13_separate_interpretations(self):
        ref_friday = datetime(2026, 11, 6, 10, 0, tzinfo=APP_TIMEZONE)
        res = resolve_relative_deadline("delivery by Friday", reference_datetime=ref_friday)
        self.assertTrue(res["is_ambiguous"])
        self.assertIsNone(res["resolved_date"])
        self.assertIn("2026-11-06", res["interpretations"])
        self.assertIn("2026-11-13", res["interpretations"])
        self.assertEqual(len(res["interpretations"]), 2)

    # 3. An unresolved conflict preventing task creation
    def test_03_unresolved_conflict_blocks_task_creation(self):
        prop = self.task_service.propose_task(
            prospect_id="acme_rahul",
            prospect_name="Rahul Sharma",
            company="Acme",
            description="Send revised proposal",
            reason="CFO condition",
            supporting_evidence="Notes",
            due_date=None,
            has_deadline_conflict=True,
            deadline_conflict_details="Conflict: Historical Nov 13 vs latest Friday"
        )
        self.assertTrue(prop["has_deadline_conflict"])

        # Attempting to approve without confirmed date raises ValueError
        with self.assertRaises(ValueError) as ctx:
            self.task_service.approve_task(prop["proposal_id"], confirmed_due_date=None)
        self.assertIn("Unresolved deadline conflict", str(ctx.exception))

        # Supplying explicit confirmed date succeeds
        task = self.task_service.approve_task(prop["proposal_id"], confirmed_due_date="2026-11-13")
        self.assertIsNotNone(task)
        self.assertEqual(task["confirmed_due_date"], "2026-11-13")

    # 4. No unconfirmed date propagating into a task
    def test_04_no_unconfirmed_date_propagating(self):
        historical = ["Rahul requested a revised proposal by November 13, 2026."]
        self.mock_hindsight.recall.return_value = MagicMock(
            results=[MagicMock(text=historical[0], tags=["prospect:acme-technologies_rahul-sharma"])]
        )
        # Mock LLM returning 2026-11-10
        self.mock_groq.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps({
                "customer_state": {
                    "current_stage": "Pricing / Proposal",
                    "requirements": [], "interests": [], "objections": [], "budget_concerns": [],
                    "decisions": [], "decision_stages": [], "document_lifecycle": [],
                    "customer_requests": [], "team_promises": [], "scheduled_actions": [],
                    "completed_actions": [], "unresolved_actions": [], "conflicts_detected": []
                },
                "next_best_action": {
                    "recommended_action": "Send revised proposal to Rahul Sharma",
                    "why_this_action": "CFO conditional approval",
                    "supporting_evidence": "Notes",
                    "relevant_dates": "2026-11-10",
                    "current_status": "Status not confirmed",
                    "responsible_person": "Sales Team",
                    "timing": "2026-11-10",
                    "confidence": "High",
                    "if_already_completed": "Confirm receipt"
                },
                "proposed_task_tool": {
                    "tool_name": "create_followup_task",
                    "description": "Send revised proposal to Rahul Sharma by Friday",
                    "reason": "CFO conditional approval",
                    "supporting_evidence": "Notes",
                    "due_date": "2026-11-10",
                    "responsible_person": "Sales Team"
                }
            })))]
        )

        res = self.agent.process_meeting(
            name="Rahul Sharma",
            company="Acme Technologies",
            goal="Discuss pricing",
            notes="CFO conditional approval requires delivery by Friday."
        )

        # 1. Proposed task must NOT contain 2026-11-10
        prop = res["proposed_task"]
        self.assertIsNotNone(prop)
        self.assertIsNone(prop["due_date"])
        self.assertIsNone(prop["confirmed_due_date"])
        self.assertTrue(prop["has_deadline_conflict"])

        # 2. NBA timing must be "Deadline not confirmed"
        nba = res["next_best_action"]
        self.assertEqual(nba["relevant_dates"], "Deadline not confirmed")
        self.assertEqual(nba["timing"], "Deadline not confirmed")

        # 3. Briefing must not contain "2026-11-10"
        self.assertNotIn("2026-11-10", res["brief"])

    # 5. Existing historical tasks being preserved
    def test_05_existing_historical_tasks_preserved(self):
        # Insert historical task with unconfirmed due_date 2026-11-10
        conn = self.db.get_conn()
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        cur.execute("""
        INSERT INTO tasks (
            task_id, proposal_id, prospect_id, prospect_name, company,
            description, reason, supporting_evidence, due_date, responsible_person,
            task_status, approval_status, outcome_note, outcome_recorded_at,
            created_at, updated_at
        ) VALUES (
            'TASK-HIST-01', 'PROP-HIST-01', 'acme_rahul', 'Rahul Sharma', 'Acme',
            'Send revised proposal', 'CFO approval', 'Notes', '2026-11-10', 'Sales Team',
            'Completed', 'approved', 'It has been a great pleasure working with you!', ?,
            ?, ?
        )
        """, (now, now, now))
        conn.commit()
        conn.close()

        # Fetch via database manager
        task = self.db.get_task("TASK-HIST-01")
        self.assertIsNotNone(task)
        # Raw record is preserved exactly as stored
        self.assertEqual(task["due_date"], "2026-11-10")
        self.assertEqual(task["task_status"], "Completed")
        self.assertEqual(task["outcome_note"], "It has been a great pleasure working with you!")
        # Formatted display deadline is marked unconfirmed, preventing presentation as confirmed
        self.assertEqual(task["display_deadline"], "Deadline not confirmed")
        self.assertFalse(task["is_date_confirmed"])

    # 6. Completed internal tasks not automatically completing document delivery
    def test_06_internal_task_completion_does_not_advance_document_delivery(self):
        # Even if a task is marked completed, generic notes do NOT advance document state to Delivered
        notes = "Follow up with Rahul. Latest notes mention pricing proposal was promised."
        memories = [
            "A customized pricing proposal was promised to Rahul Sharma by Friday.",
            "CONFIRMED TASK OUTCOME: Action/Task Completed: Send revised proposal. Confirmed Outcome Note: Great working with you!"
        ]
        res = self.ai_service._build_fallback_reasoning(
            prospect_name="Rahul Sharma",
            company="Acme",
            meeting_goal="Review pricing",
            latest_notes=notes,
            memories=memories
        )
        doc_lifecycle = res["customer_state"]["document_lifecycle"]
        prop_doc = next((d for d in doc_lifecycle if "Proposal" in d["document"]), None)
        self.assertIsNotNone(prop_doc)
        # State remains Promised, NOT Delivered or Received!
        self.assertEqual(prop_doc["state"], "Promised")
        self.assertNotEqual(prop_doc["state"], "Delivered")
        self.assertNotEqual(prop_doc["state"], "Received")

    # 7. Semantic duplicates being removed from displayed memories
    def test_07_semantic_duplicates_removed(self):
        p_id = normalize_prospect_id("Rahul Sharma", "Acme")
        raw = [
            # With Hindsight trailer vs without
            {"id": "1", "text": "The sales team agreed to send a revised proposal to Rahul Sharma. | When: 2026-11-06 | Involving: Rahul Sharma | To address pricing objections and finalize terms."},
            {"id": "2", "text": "The sales team agreed to send a revised proposal to Rahul Sharma to address pricing objections and finalize terms."},
            # Finance manager approval with trailer vs without
            {"id": "3", "text": "The finance manager at Acme Technologies approved the customized pricing proposal, contingent on the inclusion of onboarding support. | Involving: Rahul Sharma, finance manager (Acme Technologies)"},
            {"id": "4", "text": "Rahul Sharma (Acme Technologies) received approval for a customised pricing proposal from his finance manager, contingent on the provision of onboarding support. | When: 2026-11-05 | Involving: Rahul Sharma, finance manager at Acme Technologies"},
            {"id": "5", "text": "Rahul Sharma's customised pricing proposal from his finance manager was approved, contingent on the provision of onboarding support."}
        ]
        cleaned = clean_and_deduplicate_memories(raw, p_id, "Rahul Sharma", "Acme")
        # Sales team commitment deduplicates to 1; Finance manager approval deduplicates to 1
        self.assertEqual(len(cleaned), 2)

    # 8. Distinct approval events remaining separate
    def test_08_distinct_approval_events_remain_separate(self):
        p_id = normalize_prospect_id("Rahul Sharma", "Acme")
        raw = [
            # CFO conditional approval (contingent on Friday delivery and 24/7 SLA)
            {"id": "1", "text": "Rahul Sharma's CFO gave conditional approval for enterprise pricing, contingent on a Friday delivery and 24/7 SLA. | When: 2026-11-06 | Involving: Rahul Sharma"},
            # Finance manager approval (contingent on onboarding support)
            {"id": "2", "text": "The finance manager at Acme Technologies approved the customized pricing proposal, contingent on the inclusion of onboarding support. | Involving: Rahul Sharma, finance manager (Acme Technologies)"}
        ]
        cleaned = clean_and_deduplicate_memories(raw, p_id, "Rahul Sharma", "Acme")
        # Distinct decision-makers and conditions MUST both be preserved!
        self.assertEqual(len(cleaned), 2)
        texts = [c["text"] for c in cleaned]
        self.assertTrue(any("CFO" in t or "24/7" in t for t in texts))
        self.assertTrue(any("finance manager" in t or "onboarding" in t for t in texts))

    # 9. Hindsight sync retry working without duplicate records
    def test_09_hindsight_sync_retry_without_duplicates(self):
        # 1. Propose and approve a task
        prop = self.task_service.propose_task(
            prospect_id="acme_rahul",
            prospect_name="Rahul Sharma",
            company="Acme",
            description="Send onboarding plan",
            reason="Customer request",
            supporting_evidence="Notes"
        )
        task = self.task_service.approve_task(prop["proposal_id"])
        task_id = task["task_id"]

        # 2. Simulate initial sync failure (e.g. network blip)
        self.mock_hindsight.retain.side_effect = Exception("Timeout during initial retain")
        res1 = self.task_service.update_outcome(
            task_id=task_id,
            new_status="Completed",
            outcome_note="Onboarding plan sent on Nov 06."
        )
        self.assertEqual(res1["hindsight_sync_status"], "failed")
        self.assertIn("Timeout", res1["hindsight_sync_error"])

        # 3. Retry sync succeeds
        self.mock_hindsight.retain.side_effect = None
        self.mock_hindsight.retain.return_value = MagicMock(id="mem-12345")
        res2 = self.task_service.retry_hindsight_sync(task_id=task_id)
        self.assertTrue(res2["success"])
        self.assertEqual(res2["task"]["hindsight_sync_status"], "synced")
        self.assertIsNone(res2["task"]["hindsight_sync_error"])

        # Retain was called with idempotent document_id
        retain_call = self.mock_hindsight.retain.call_args[1]
        self.assertEqual(retain_call["document_id"], f"outcome-{task_id}")

        # 4. Calling retry again when already synced is a no-op (prevents duplicate retries)
        self.mock_hindsight.retain.reset_mock()
        res3 = self.task_service.retry_hindsight_sync(task_id=task_id)
        self.assertTrue(res3["success"])
        self.mock_hindsight.retain.assert_not_called()


if __name__ == "__main__":
    unittest.main()
