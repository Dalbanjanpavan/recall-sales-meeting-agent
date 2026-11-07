"""Regression coverage for saved original Meeting Intelligence history."""

import os
import tempfile
import unittest

from ai_service import AIService
from app import create_app
from database import DatabaseManager
from agent_service import RecallAgent


class OfflineHindsight:
    """No-network Hindsight boundary for meeting history tests."""

    is_connected = False
    last_recall_status = "unavailable"
    last_retain_status = "not_attempted"

    def search_memories(self, **kwargs):
        return [], 0, 0


class MeetingHistoryTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = handle.name
        self.db = DatabaseManager(self.db_path)
        self.agent = RecallAgent(
            db_manager=self.db,
            hindsight_service=OfflineHindsight(),
            ai_service=AIService(offline_mode=True),
        )
        self.app = create_app(
            db_manager=self.db,
            hindsight_service=OfflineHindsight(),
            ai_service=AIService(offline_mode=True),
        )
        self.app.extensions["recall_agent"] = self.agent
        self.client = self.app.test_client()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_original_meetings_are_saved_and_reopened_in_date_order(self):
        first = self.agent.process_meeting(
            name="Arjun Mehta",
            company="BrightPath Retail",
            goal="Understand support challenges",
            notes=(
                "On November 7, 2026, Arjun Mehta discussed an Enterprise AI Customer "
                "Support Chatbot, WhatsApp integration, and Shopify order tracking."
            ),
        )
        second = self.agent.process_meeting(
            name="Arjun Mehta",
            company="BrightPath Retail",
            goal="Discuss negotiation and objections",
            notes=(
                "On November 12, 2026, Arjun Mehta requested a ₹9 lakh quotation and "
                "a 30-day pilot. The CFO considered ₹12 lakh too expensive."
            ),
        )

        history_response = self.client.get("/api/meetings")
        self.assertEqual(history_response.status_code, 200)
        meetings = history_response.get_json()["meetings"]
        self.assertEqual([item["meeting_date"] for item in meetings], ["2026-11-12", "2026-11-07"])
        self.assertEqual(meetings[0]["prospect_name"], "Arjun Mehta")
        self.assertIn("₹9 lakh", meetings[0]["notes"])
        self.assertIn("WhatsApp integration", meetings[1]["notes"])

        reopened = self.client.get(f"/api/meetings/{first['meeting_id']}")
        self.assertEqual(reopened.status_code, 200)
        record = reopened.get_json()["meeting"]
        self.assertEqual(record["meeting_date"], "2026-11-07")
        self.assertIn("Shopify order tracking", record["notes"])
        self.assertEqual(second["meeting_id"], meetings[0]["meeting_id"])

    def test_same_meeting_is_not_duplicated_when_reanalysed(self):
        details = {
            "name": "Arjun Mehta",
            "company": "BrightPath Retail",
            "goal": "Initial discovery",
            "notes": "On November 7, 2026, Arjun Mehta requested a customer support chatbot.",
        }
        self.agent.process_meeting(**details)
        self.agent.process_meeting(**details)
        meetings = self.client.get("/api/meetings").get_json()["meetings"]
        self.assertEqual(len(meetings), 1)

    def test_selected_meeting_date_is_saved_when_notes_do_not_contain_a_date(self):
        result = self.agent.process_meeting(
            name="Arjun Mehta",
            company="BrightPath Retail",
            goal="Discuss final terms",
            notes="Arjun confirmed the revised price and pilot terms still need review.",
            meeting_date="2026-11-12",
        )
        meeting = self.client.get(f"/api/meetings/{result['meeting_id']}").get_json()["meeting"]
        self.assertEqual(meeting["meeting_date"], "2026-11-12")

    def test_demo_and_preliminary_pricing_promise_do_not_become_completed_or_objection(self):
        result = self.agent.process_meeting(
            name="Dr. Neha Kapoor",
            company="NovaCare Hospitals",
            goal="Understand patient-support needs",
            meeting_date="2026-11-07",
            notes=(
                "Dr. Neha Kapoor discussed a patient support chatbot with CloseAI. "
                "NovaCare needs website and WhatsApp support in English and Kannada, "
                "with human handover and no medical diagnosis. CloseAI agreed to prepare "
                "a customized product demonstration and preliminary pricing proposal by November 10, 2026."
            ),
        )
        state = result["customer_state"]
        self.assertEqual(state["objections"], [])
        self.assertEqual(state["budget_concerns"], [])
        self.assertEqual(state["completed_actions"], [])
        self.assertTrue(any("demonstration" in item.lower() for item in state["team_promises"]))
        self.assertEqual(state["document_lifecycle"][0]["state"], "Promised")
        self.assertIn("Prepare and send", result["next_best_action"]["recommended_action"])


if __name__ == "__main__":
    unittest.main()
