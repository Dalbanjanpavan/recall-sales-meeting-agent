"""
End-to-End API Integration Test for Recall Flask Application.
Tests the complete HTTP workflow:
- POST /api/prepare
- POST /api/tasks/reject
- POST /api/tasks/approve
- GET /api/tasks
- POST /api/tasks/<id>/outcome
- GET /api/prospect/<id>/state
Uses isolated temporary test database and mocked external services.
"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock
from app import app, agent
from database import DatabaseManager, init_db


class TestAppIntegration(unittest.TestCase):

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

        # Create isolated temporary database for test run
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        init_db(self.temp_db_path)
        self.test_db = DatabaseManager(self.temp_db_path)

        # Point agent and task service to temporary database
        self.orig_db = agent.db
        agent.db = self.test_db
        agent.tasks.db = self.test_db

        # Mock out external API clients
        self.orig_ai = agent.ai
        self.orig_hindsight = agent.hindsight

        self.mock_ai = MagicMock()
        self.mock_hindsight = MagicMock()

        agent.ai = self.mock_ai
        agent.hindsight = self.mock_hindsight
        agent.tasks.hindsight = self.mock_hindsight

    def tearDown(self):
        agent.db = self.orig_db
        agent.tasks.db = self.orig_db
        agent.ai = self.orig_ai
        agent.hindsight = self.orig_hindsight
        agent.tasks.hindsight = self.orig_hindsight

        os.close(self.temp_db_fd)
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    def test_full_agent_workflow(self):
        self.mock_hindsight.search_memories.return_value = (
            ["Prospect: Priya at ZetaCorp requested an ROI calculator on Oct 10."],
            1,
            1
        )

        self.mock_ai.analyze_and_reason.return_value = {
            "customer_state": {
                "current_stage": "Pricing / Proposal",
                "requirements": ["Enterprise SLA", "ROI model"],
                "objections": [],
                "decisions": [],
                "customer_requests": ["Send custom ROI calculator"],
                "team_promises": ["Deliver ROI calculator by Friday"],
                "scheduled_actions": [],
                "completed_actions": [],
                "unresolved_actions": ["Deliver ROI model"],
                "conflicts_detected": []
            },
            "next_best_action": {
                "recommended_action": "Deliver verified enterprise ROI model to Priya",
                "why_this_action": "Sales team promised delivery by Friday",
                "supporting_evidence": "Notes from Oct 10",
                "relevant_dates": "Friday",
                "current_status": "Status not confirmed",
                "responsible_person": "Not recorded",
                "timing": "Friday",
                "confidence": "High",
                "if_already_completed": "Confirm receipt and review calculations together"
            },
            "proposed_task_tool": {
                "tool_name": "create_followup_task",
                "description": "Send verified enterprise ROI model to Priya",
                "reason": "Sales team promised delivery by Friday",
                "supporting_evidence": "Notes from Oct 10",
                "due_date": "Friday",
                "responsible_person": None
            }
        }
        self.mock_ai.generate_meeting_brief.return_value = "# Briefing for Priya at ZetaCorp"

        # 1. Step 1: POST /api/prepare
        resp = self.client.post("/api/prepare", json={
            "name": "Priya Sharma",
            "company": "ZetaCorp",
            "goal": "Review ROI model and finalize budget",
            "notes": "Priya asked if we had finalized the ROI calculations promised earlier."
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("proposed_task", data)
        self.assertIsNotNone(data["proposed_task"])
        proposal_id = data["proposed_task"]["proposal_id"]

        # 2. Step 2: Test Rejection
        rej_resp = self.client.post("/api/tasks/reject", json={
            "proposal_id": proposal_id,
            "reason": "Decided to cover live during the meeting"
        })
        self.assertEqual(rej_resp.status_code, 200)
        rej_data = rej_resp.get_json()
        self.assertEqual(rej_data["status"], "rejected")

        # 3. Verify no task created
        tasks_resp = self.client.get(f"/api/tasks?prospect_id={data['prospect_id']}")
        self.assertEqual(tasks_resp.status_code, 200)
        self.assertEqual(len(tasks_resp.get_json()["tasks"]), 0)

        # 4. Step 3: Propose a new task and approve it
        self.mock_ai.analyze_and_reason.return_value["proposed_task_tool"]["description"] = "Send updated SOC2 compliance packet"
        resp2 = self.client.post("/api/prepare", json={
            "name": "Priya Sharma",
            "company": "ZetaCorp",
            "goal": "Review security terms",
            "notes": "Discussion moved forward, need follow-up on SOC2 packet."
        })
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        prop2_id = data2["proposed_task"]["proposal_id"]

        # Approve the task
        app_resp = self.client.post("/api/tasks/approve", json={"proposal_id": prop2_id})
        self.assertEqual(app_resp.status_code, 200)
        app_data = app_resp.get_json()
        self.assertTrue(app_data["success"])
        task = app_data["task"]
        task_id = task["task_id"]

        # 5. Verify task is in persistent list
        tasks_resp2 = self.client.get(f"/api/tasks?prospect_id={data['prospect_id']}")
        tasks = tasks_resp2.get_json()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], task_id)
        self.assertEqual(tasks[0]["task_status"], "Pending")

        # 6. Step 4: Update task outcome
        outcome_resp = self.client.post(f"/api/tasks/{task_id}/outcome", json={
            "status": "Completed",
            "outcome_note": "SOC2 packet delivered and accepted."
        })
        self.assertEqual(outcome_resp.status_code, 200)
        outcome_data = outcome_resp.get_json()
        self.assertTrue(outcome_data["success"])
        self.assertEqual(outcome_data["task"]["task_status"], "Completed")
        self.assertEqual(outcome_data["task"]["outcome_note"], "SOC2 packet delivered and accepted.")

        # Verify Hindsight retention was invoked
        self.mock_hindsight.save_confirmed_outcome.assert_called()

        # 7. Step 5: Check customer state
        state_resp = self.client.get(f"/api/prospect/{data['prospect_id']}/state")
        self.assertEqual(state_resp.status_code, 200)
        state_data = state_resp.get_json()["state"]
        self.assertEqual(state_data["prospect_name"], "Priya Sharma")
        self.assertEqual(state_data["company"], "ZetaCorp")


if __name__ == "__main__":
    unittest.main()
