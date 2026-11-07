"""
Comprehensive Automated Test Suite for Recall — Sales Meeting Intelligence Agent.
Covers all 10 required architectural and behavioral tests:
- TEST 1: New prospect isolation
- TEST 2: Multiple meetings chronological evolution
- TEST 3: Duplicate memories handling
- TEST 4: Conflicting information resolution
- TEST 5: Unconfirmed promise validation
- TEST 6: Completed action prevention
- TEST 7: Human approval rejection
- TEST 8: Approved action single task creation
- TEST 9: Outcome learning & retention in Hindsight
- TEST 10: Application restart persistence
"""

import os
import unittest
import tempfile
import sqlite3
from unittest.mock import MagicMock

from database import DatabaseManager, init_db
from memory_processor import (
    normalize_prospect_id,
    clean_and_deduplicate_memories,
    detect_memory_conflicts,
    is_prospect_match
)
from hindsight_service import HindsightService
from ai_service import AIService
from task_service import TaskService
from agent_service import RecallAgent


class TestRecallAgent(unittest.TestCase):

    def setUp(self):
        # Create an isolated temporary SQLite database for each test run
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        init_db(self.temp_db_path)
        self.db = DatabaseManager(self.temp_db_path)

        # Mock Hindsight Client to prevent network overhead and bank pollution
        self.mock_hindsight_client = MagicMock()
        self.hindsight_service = HindsightService(client=self.mock_hindsight_client)

        # Mock Groq Client for deterministic reasoning tests
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

    def test_01_new_prospect_isolation(self):
        """
        TEST 1 — New prospect:
        A new prospect should have no previous customer history and must not
        receive another prospect's information.
        """
        # Memory pool containing records for a different prospect: Alice at Stark Corp
        other_prospect_memories = [
            {"id": "m1", "text": "Prospect: Alice. Stark Corp. Alice approved the $50k contract.", "tags": ["prospect:stark-corp_alice"]},
            {"id": "m2", "text": "Prospect: Bob. Wayne Enterprises. Bob requested security audit.", "tags": ["prospect:wayne-enterprises_bob"]}
        ]

        new_prospect_id = normalize_prospect_id("Rahul Sharma", "Acme Technologies")
        cleaned = clean_and_deduplicate_memories(
            raw_memories=other_prospect_memories,
            prospect_id=new_prospect_id,
            prospect_name="Rahul Sharma",
            company="Acme Technologies"
        )

        # Strict isolation: New prospect must receive ZERO memories from other prospects
        self.assertEqual(len(cleaned), 0)

        # Also verify database has no previous state for this prospect
        state = self.db.get_customer_state(new_prospect_id)
        self.assertIsNone(state)

    def test_02_multiple_meetings_chronological_evolution(self):
        """
        TEST 2 — Multiple meetings:
        Recall should correctly understand a prospect's developments across at least three meetings.
        """
        prospect_id = normalize_prospect_id("Vikram Patel", "Globex Corp")

        # Meeting 1: Initial Discovery
        state1 = {
            "prospect_id": prospect_id,
            "prospect_name": "Vikram Patel",
            "company": "Globex Corp",
            "current_stage": "Discovery",
            "requirements": ["Cloud migration"],
            "completed_actions": [],
            "unresolved_actions": ["Conduct product demonstration"]
        }
        self.db.save_customer_state(state1)

        # Meeting 2: Demonstration Completed, Pricing Concerns
        state2 = {
            "prospect_id": prospect_id,
            "prospect_name": "Vikram Patel",
            "company": "Globex Corp",
            "current_stage": "Demonstration",
            "requirements": ["Cloud migration"],
            "objections": ["Pricing is 20% higher than competitor"],
            "completed_actions": ["Product demonstration completed"],
            "unresolved_actions": ["Prepare customized pricing proposal"]
        }
        self.db.save_customer_state(state2)

        # Meeting 3: Proposal Delivered, Awaiting Finance Approval
        state3 = {
            "prospect_id": prospect_id,
            "prospect_name": "Vikram Patel",
            "company": "Globex Corp",
            "current_stage": "Pricing / Proposal",
            "requirements": ["Cloud migration"],
            "objections": [],
            "completed_actions": ["Product demonstration completed", "Customized proposal delivered"],
            "unresolved_actions": ["Awaiting Vikram's internal finance review"]
        }
        self.db.save_customer_state(state3)

        # Verify final chronological state
        saved_state = self.db.get_customer_state(prospect_id)
        self.assertEqual(saved_state["current_stage"], "Pricing / Proposal")
        self.assertIn("Customized proposal delivered", saved_state["completed_actions"])
        self.assertEqual(len(saved_state["completed_actions"]), 2)

    def test_03_duplicate_memories_deduplication(self):
        """
        TEST 3 — Duplicate memories:
        Repeated records should not create repeated commitments or actions.
        """
        p_id = normalize_prospect_id("Sarah Connor", "Cyberdyne")

        # Duplicate inputs: identical and near-identical memories
        raw = [
            {"id": "1", "text": "Prospect: Sarah Connor at Cyberdyne requested security whitepaper on Oct 12."},
            {"id": "2", "text": "Prospect: Sarah Connor at Cyberdyne requested security whitepaper on Oct 12."}, # Exact duplicate
            {"id": "3", "text": "Prospect: Sarah Connor at Cyberdyne requested security whitepaper on Oct 12!"}, # Whitespace/punct near-dup
            {"id": "4", "text": "Prospect: Sarah Connor at Cyberdyne confirmed SOC2 audit passed on Nov 01."}  # Distinct event
        ]

        cleaned = clean_and_deduplicate_memories(
            raw_memories=raw,
            prospect_id=p_id,
            prospect_name="Sarah Connor",
            company="Cyberdyne"
        )

        # Should deduplicate to 2 distinct events
        self.assertEqual(len(cleaned), 2)
        texts = [m["text"] for m in cleaned]
        self.assertTrue(any("whitepaper" in t for t in texts))
        self.assertTrue(any("SOC2" in t for t in texts))

    def test_04_conflicting_information_resolution(self):
        """
        TEST 4 — Conflicting information:
        An older customer status should not override a newer confirmed status.
        """
        memories = [
            {"text": "Prospect: David at Apex. Initial call: David expressed serious pricing objection, budget not approved."},
            {"text": "Prospect: David at Apex. Meeting 3: Revised quote approved by CFO, pricing approved."}
        ]

        conflicts = detect_memory_conflicts(memories)
        self.assertGreater(len(conflicts), 0)
        self.assertEqual(conflicts[0]["type"], "pricing_status_conflict")
        self.assertIn("latest confirmed status supersedes", conflicts[0]["description"])

    def test_05_unconfirmed_promise(self):
        """
        TEST 5 — Unconfirmed promise:
        A promised proposal with no delivery confirmation must not be marked completed or scheduled.
        """
        p_name = "Arjun Nair"
        comp = "Apex Labs"
        notes = "Sales team promised to send customized pricing proposal by Friday. No delivery confirmed."

        reasoning = self.ai_service._build_fallback_reasoning(
            prospect_name=p_name,
            company=comp,
            meeting_goal="Follow-up on pricing",
            latest_notes=notes,
            memories=[]
        )

        # Next best action must recommend verifying delivery, NOT assuming delivered or completed
        rec = reasoning["next_best_action"]
        self.assertIn("Verify whether the proposal", rec["recommended_action"])
        self.assertEqual(rec["current_status"], "Status not confirmed")
        self.assertNotIn("Completed", reasoning["customer_state"]["completed_actions"])

    def test_06_completed_action_not_recommended_again(self):
        """
        TEST 6 — Completed action:
        A confirmed completed action must not be recommended again.
        """
        p_name = "Meera Reddy"
        comp = "Zenith"
        memories = [
            "Prospect: Meera Reddy at Zenith. Product demonstration was completed on Nov 02.",
            "CONFIRMED TASK OUTCOME: Demonstration completed. Meera loved the UI."
        ]

        # The AI fallback should not recommend doing the demonstration again
        reasoning = self.ai_service._build_fallback_reasoning(
            prospect_name=p_name,
            company=comp,
            meeting_goal="Next steps",
            latest_notes="Discuss next steps after demonstration",
            memories=memories
        )

        rec = reasoning["next_best_action"]["recommended_action"]
        self.assertNotIn("Schedule demonstration", rec)

    def test_07_human_approval_rejection_creates_no_task(self):
        """
        TEST 7 — Human approval:
        Rejecting a proposed task must not create it in SQLite tasks table.
        """
        p_id = normalize_prospect_id("Kavita Rao", "InnoTech")
        proposal = self.task_service.propose_task(
            prospect_id=p_id,
            prospect_name="Kavita Rao",
            company="InnoTech",
            description="Send technical whitepaper",
            reason="Customer expressed interest in architectural diagrams",
            supporting_evidence="Meeting notes dated Nov 04"
        )

        proposal_id = proposal["proposal_id"]

        # Salesperson Rejects the proposal
        res = self.task_service.reject_task(proposal_id=proposal_id, reason="Customer found document online")
        self.assertEqual(res["status"], "rejected")

        # Verify proposal marked rejected
        prop_db = self.db.get_proposal(proposal_id)
        self.assertEqual(prop_db["status"], "rejected")

        # Verify NO persistent task was created
        tasks = self.db.list_tasks(prospect_id=p_id)
        self.assertEqual(len(tasks), 0)

    def test_08_approved_action_creates_exactly_one_persistent_task(self):
        """
        TEST 8 — Approved action:
        Approving a task must create exactly one persistent task with unique ID.
        Repeated approval must be blocked.
        """
        p_id = normalize_prospect_id("Rohan Mehta", "CloudCore")
        proposal = self.task_service.propose_task(
            prospect_id=p_id,
            prospect_name="Rohan Mehta",
            company="CloudCore",
            description="Schedule security architecture review",
            reason="CISO review required for enterprise rollout",
            supporting_evidence="Customer request from meeting 1"
        )

        proposal_id = proposal["proposal_id"]

        # 1. First Approval -> Creates task
        created_task = self.task_service.approve_task(proposal_id=proposal_id)
        self.assertIsNotNone(created_task)
        self.assertTrue(created_task["task_id"].startswith("TASK-"))
        self.assertEqual(created_task["task_status"], "Pending")

        # 2. Verify exactly one task in DB
        tasks = self.db.list_tasks(prospect_id=p_id)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], created_task["task_id"])

        # 3. Second Approval attempt on same proposal must be rejected (idempotency / duplicate protection)
        with self.assertRaises(ValueError):
            self.task_service.approve_task(proposal_id=proposal_id)

        # Still only one task exists
        self.assertEqual(len(self.db.list_tasks(prospect_id=p_id)), 1)

    def test_09_outcome_learning_retains_to_hindsight(self):
        """
        TEST 9 — Outcome learning:
        A user-confirmed task outcome must be retained in Hindsight and available in future briefings.
        """
        p_id = normalize_prospect_id("Ananya Sen", "DataFlow")
        proposal = self.task_service.propose_task(
            prospect_id=p_id,
            prospect_name="Ananya Sen",
            company="DataFlow",
            description="Verify if security checklist was submitted",
            reason="Compliance requirement",
            supporting_evidence="Meeting notes Nov 03"
        )
        task = self.task_service.approve_task(proposal["proposal_id"])
        task_id = task["task_id"]

        # Salesperson confirms outcome
        update_res = self.task_service.update_outcome(
            task_id=task_id,
            new_status="Completed",
            outcome_note="Security checklist confirmed submitted and approved by IT on Nov 05."
        )

        self.assertTrue(update_res["success"])
        # Verify Hindsight retain was called with tagged prospect information
        self.mock_hindsight_client.retain.assert_called()
        call_kwargs = self.mock_hindsight_client.retain.call_args[1]
        self.assertIn("CONFIRMED TASK OUTCOME", call_kwargs["content"])
        self.assertIn("Security checklist confirmed", call_kwargs["content"])
        self.assertIn(f"prospect:{p_id}", call_kwargs["tags"])

    def test_10_application_restart_persistence(self):
        """
        TEST 10 — Application restart:
        Customer state and tasks must remain available after restarting the application.
        """
        p_id = normalize_prospect_id("Dev Sharma", "OmniNet")

        # 1. Save customer state and create a task
        state_data = {
            "prospect_id": p_id,
            "prospect_name": "Dev Sharma",
            "company": "OmniNet",
            "current_stage": "Negotiation",
            "requirements": ["Multi-tenant SSO", "SLA 99.99%"],
            "completed_actions": ["Completed security assessment"],
            "unresolved_actions": ["Finalize commercial discount"]
        }
        self.db.save_customer_state(state_data)

        prop = self.task_service.propose_task(
            prospect_id=p_id,
            prospect_name="Dev Sharma",
            company="OmniNet",
            description="Finalize SLA term sheet",
            reason="Required before contract sign-off",
            supporting_evidence="Legal review request"
        )
        created_task = self.task_service.approve_task(prop["proposal_id"])

        # 2. Simulate complete application restart by instantiating a brand new DatabaseManager
        # pointing to the same SQLite file
        restarted_db = DatabaseManager(self.temp_db_path)

        # 3. Retrieve state and tasks through restarted manager
        recovered_state = restarted_db.get_customer_state(p_id)
        self.assertIsNotNone(recovered_state)
        self.assertEqual(recovered_state["current_stage"], "Negotiation")
        self.assertIn("Multi-tenant SSO", recovered_state["requirements"])

        recovered_tasks = restarted_db.list_tasks(prospect_id=p_id)
        self.assertEqual(len(recovered_tasks), 1)
        self.assertEqual(recovered_tasks[0]["task_id"], created_task["task_id"])
        self.assertEqual(recovered_tasks[0]["description"], "Finalize SLA term sheet")


if __name__ == "__main__":
    unittest.main()
