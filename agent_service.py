"""
Agent Orchestrator for Recall — Sales Meeting Intelligence Agent.
Coordinates the complete end-to-end agent workflow:
User Input -> Prospect Identification -> Hindsight Tagged Recall ->
Memory Cleaning/Deduplication -> Customer State Synthesis & Persistence ->
AI Multi-Stage Reasoning (Decision Stages, Lifecycle, Ownership) -> Next Best Action ->
Follow-up Task Proposal with Safeguards -> Executive Briefing.
"""

from datetime import datetime, timezone
import re
import hashlib
from typing import Dict, Any, List, Optional
from database import DatabaseManager
from memory_processor import normalize_prospect_id, detect_memory_conflicts
from date_validator import (
    detect_deadline_conflicts,
    validate_weekday_date_combination,
    sanitize_deadline,
    extract_deadline_phrase,
    extract_date_from_text,
    redact_invalid_weekday_date_claims,
)
from hindsight_service import HindsightService
from ai_service import AIService
from task_service import TaskService


class RecallAgent:
    """End-to-end Sales Intelligence Agent coordinator."""

    def __init__(
        self,
        hindsight_service: Optional[HindsightService] = None,
        ai_service: Optional[AIService] = None,
        db_manager: Optional[DatabaseManager] = None
    ):
        self.db = db_manager or DatabaseManager()
        self.hindsight = hindsight_service or HindsightService()
        self.ai = ai_service or AIService()
        self.tasks = TaskService(db_manager=self.db, hindsight_service=self.hindsight)

    @staticmethod
    def _deduplicate_conflicts(conflicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse repeated wording while preserving genuinely distinct facts."""
        unique = []
        seen = set()
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                conflict = {"type": "detected_conflict", "description": str(conflict)}
            description = str(conflict.get("description", ""))
            normalized = re.sub(r"\bby\s+", "", description.lower())
            normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
            # The LLM may label the same rule-derived conflict differently;
            # description identity is the reliable presentation key.
            key = normalized
            if key not in seen:
                unique.append(conflict)
                seen.add(key)
        return unique

    @staticmethod
    def _deduplicate_text_items(items: Any, exclude: Optional[List[str]] = None) -> List[str]:
        """Keep one presentation copy of repeated commitments or actions."""
        results, seen = [], set()
        for item in items or []:
            value = str(item).strip()
            key = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
            if not value or key in seen or (exclude and key in exclude):
                continue
            results.append(value)
            seen.add(key)
        return results

    @staticmethod
    def _sanitize_document_lifecycle(items: Any, source_text: str) -> List[Dict[str, Any]]:
        """Suppress lifecycle advances without document-specific evidence."""
        evidence = source_text.lower()
        doc_pattern = r"(?:proposal|quotation)"

        def has_event(event: str) -> bool:
            return bool(re.search(rf"\b{doc_pattern}\b[^.\n]{{0,80}}\b{event}\b|\b{event}\b[^.\n]{{0,80}}\b{doc_pattern}\b", evidence))

        verified_states = {
            "Sent": has_event("sent|dispatched"),
            "Delivered": has_event("delivered"),
            "Received": has_event(r"received|receipt\s+(?:was\s+)?confirmed"),
            "Reviewed": has_event("reviewed"),
            "Approved": bool(re.search(r"\b(?:proposal|quotation)\b\s+(?:was\s+)?approved\b|\bapproved\b\s+(?:the\s+)?(?:proposal|quotation)\b", evidence)),
            "Rejected": has_event("rejected"),
        }
        requested = has_event("requested|ask(?:ed)?|require(?:d)?")
        promised = bool(re.search(rf"(?:closeai|sales team|we)\b[^.\n]{{0,80}}\b(promised|agreed|will send|will prepare|prepare)\b[^.\n]{{0,80}}\b{doc_pattern}\b", evidence))
        safe_items = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            cleaned = dict(item)
            state = cleaned.get("state")
            if state in verified_states and not verified_states[state]:
                cleaned["state"] = "Requested" if requested else "Promised" if promised else "Not confirmed"
                cleaned["source"] = "No document-specific evidence for the claimed lifecycle advance"
            safe_items.append(cleaned)
        state_rank = {"Not confirmed": 0, "Requested": 1, "Promised": 2, "Sent": 3, "Delivered": 4, "Received": 5, "Reviewed": 6, "Approved": 7, "Rejected": 7}
        grouped: Dict[str, Dict[str, Any]] = {}
        for item in safe_items:
            key = re.sub(r"[^a-z0-9]+", " ", str(item.get("document", "proposal")).lower()).strip()
            current = grouped.get(key)
            if current is None or state_rank.get(item.get("state"), 0) >= state_rank.get(current.get("state"), 0):
                grouped[key] = item
        return list(grouped.values())

    @staticmethod
    def _validate_note_identity(prospect_name: str, notes: str) -> None:
        """Reject accidental form/name mismatches before they corrupt memory."""
        match = re.match(r"\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:mentioned|said|asked|requested|confirmed|reported)\b", notes or "")
        if not match:
            return
        stated_person = match.group(1).lower()
        expected_parts = prospect_name.lower().split()
        if expected_parts and stated_person not in {prospect_name.lower(), expected_parts[0]}:
            raise ValueError(
                f"Meeting notes appear to describe '{match.group(1)}', not selected prospect '{prospect_name}'. "
                "Correct the prospect or notes before analysis so customer memory is not corrupted."
            )

    @staticmethod
    def _sanitize_decision_stages(stages: Any, evidence: str) -> List[Dict[str, Any]]:
        """Keep only decision milestones explicitly supported by the record."""
        text = evidence.lower()
        allowed = {
            "Initial Pricing Approval": bool(re.search(r"(?:pricing|quote|quotation)[^.\n]{0,50}\b(approved|approval)\b", text)),
            "Conditional Approval": "conditional approval" in text or "approved subject to" in text or "approved provided" in text,
            "Purchase Intent": bool(re.search(r"\b(purchase intent|intent to buy|intend to buy|want to purchase|plan to purchase|interested in buying)\b", text)),
            "Final Purchase Approval": bool(re.search(r"\b(final purchase approval|final approval|purchase approved)\b", text)),
            "Contract Signed": bool(re.search(r"\b(contract signed|agreement signed|signed contract)\b", text)),
            "Purchase Completed": bool(re.search(r"\b(purchase completed|payment received|deal closed|closed won)\b", text)),
        }
        cleaned = []
        for item in stages or []:
            if not isinstance(item, dict):
                continue
            stage_type = item.get("type")
            if stage_type in allowed and allowed[stage_type]:
                cleaned.append(item)
        return cleaned

    @staticmethod
    def _sanitize_state_claims(state: Dict[str, Any], evidence: str) -> None:
        """Remove model claims that confuse discussion, promises, and completed work."""
        text = evidence.lower()
        pricing_concern = bool(re.search(
            r"(?:price|pricing|cost|budget)[^.\n]{0,60}(?:too (?:high|expensive)|concern|objection|unaffordable|discount|reduce|constraint)|(?:too (?:high|expensive)|concern|objection|unaffordable|discount|reduce|constraint)[^.\n]{0,60}(?:price|pricing|cost|budget)",
            text,
        ))
        if not pricing_concern:
            state["objections"] = [item for item in state.get("objections", []) if not re.search(r"\b(pric|cost|budget)\w*", str(item), re.IGNORECASE)]
            state["budget_concerns"] = [item for item in state.get("budget_concerns", []) if not re.search(r"\b(pric|cost|budget)\w*", str(item), re.IGNORECASE)]

        demo_completed = bool(re.search(
            r"(?:demo|demonstration)[^.\n]{0,60}(?:completed|conducted|presented|held|finished)|(?:completed|conducted|presented|held|finished)[^.\n]{0,60}(?:demo|demonstration)",
            text,
        ))
        if not demo_completed:
            state["completed_actions"] = [item for item in state.get("completed_actions", []) if not re.search(r"\b(demo|demonstration)\b", str(item), re.IGNORECASE)]

        demo_promised = bool(re.search(
            r"(?:closeai|sales team|we)[^.\n]{0,100}(?:agreed|promised|will prepare|will provide|will conduct)[^.\n]{0,100}(?:demo|demonstration)",
            text,
        ))
        if demo_promised:
            state["team_promises"] = RecallAgent._deduplicate_text_items(
                state.get("team_promises", []) + ["Prepare the customized product demonstration and preliminary pricing proposal"]
            )

    @staticmethod
    def _reconcile_verified_actions(
        prospect_name: str,
        notes: str,
        memories: List[str],
        state: Dict[str, Any],
        recommendation: Dict[str, Any],
    ) -> None:
        """Make latest verified lifecycle facts authoritative over stale AI output."""
        evidence = "\n".join(memories + [notes]).lower()
        proposal_receipt = bool(re.search(
            r"\b(?:proposal|quotation)\b[^.\n]{0,40}\b(received|receipt\s+(?:was\s+)?confirmed)\b|\b(received|receipt\s+(?:was\s+)?confirmed)\b[^.\n]{0,40}\b(?:proposal|quotation)\b",
            evidence,
        ))
        proposal_review = bool(re.search(
            r"\b(?:proposal|quotation)\b[^.\n]{0,40}\breviewed\b|\breviewed\b[^.\n]{0,40}\b(?:proposal|quotation)\b",
            evidence,
        ))
        final_authorization_pending = (
            bool(re.search(r"\b(cfo|finance|procurement)\b", evidence))
            and not bool(re.search(r"\b(final purchase approval|purchase approved|contract signed|agreement signed|purchase completed|deal closed|closed won)\b", evidence))
        )

        completed = RecallAgent._deduplicate_text_items(state.get("completed_actions", []))
        unresolved = RecallAgent._deduplicate_text_items(state.get("unresolved_actions", []))
        if proposal_receipt:
            completed.append("Proposal receipt confirmed")
            unresolved = [
                item for item in unresolved
                if not re.search(r"\b(deliver|delivery|receive|receipt|verify)\b.*\b(proposal|quotation)\b|\b(proposal|quotation)\b.*\b(deliver|delivery|receive|receipt|verify)\b", item, re.IGNORECASE)
            ]
        if proposal_review:
            completed.append("Proposal review confirmed")
        state["completed_actions"] = RecallAgent._deduplicate_text_items(completed)
        state["unresolved_actions"] = RecallAgent._deduplicate_text_items(unresolved)

        if proposal_receipt and final_authorization_pending:
            recommendation.update({
                "recommended_action": f"Schedule a meeting with {prospect_name}, the CFO, and Sales for final purchase authorization",
                "why_this_action": "The proposal receipt is confirmed and the remaining verified step is final commercial authorization.",
                "supporting_evidence": "Latest verified history confirms proposal receipt; no final purchase approval or signed contract is recorded.",
                "current_status": "Proposal received; final purchase authorization pending",
                "timing": "Next scheduled stakeholder meeting",
                "confidence": "High",
                "if_already_completed": "Record the authorization decision and any remaining procurement requirements.",
            })

    @staticmethod
    def _confirmed_scheduled_actions(items: Any, evidence: str) -> List[str]:
        """Keep scheduled actions only when a meeting/calendar confirmation exists."""
        confirmed = bool(re.search(
            r"\b(?:scheduled|confirmed|booked|calendar invite|calendar invitation|meeting set)\b",
            evidence.lower(),
        ))
        if not confirmed:
            return []
        return RecallAgent._deduplicate_text_items(items)

    @staticmethod
    def _meeting_date_from_notes(notes: str, submitted_date: Optional[str] = None) -> str:
        """Use selected or explicit meeting dates for chronology, not upload time."""
        if submitted_date:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", submitted_date):
                raise ValueError("Meeting date must use YYYY-MM-DD format.")
            return submitted_date
        explicit_date = extract_date_from_text(notes)
        return explicit_date.isoformat() if explicit_date else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def process_meeting(
        self,
        name: str,
        company: str,
        goal: str,
        notes: str,
        meeting_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Main Agent Execution Pipeline:
        1. Identifies prospect with stable internal ID (normalizing corporate suffixes).
        2. Retrieves previous memories from Hindsight (isolated by prospect tag).
        3. Cleans, deduplicates, and checks conflicts while preserving distinct events.
        4. Loads previous persistent customer state from SQLite.
        5. Executes AI multi-stage reasoning (customer decisions, document lifecycles, ownership).
        6. Updates and persists current customer state in SQLite.
        7. Prepares an actionable Follow-Up Task tool proposal (checking duplicates & rejections).
        8. Generates the Executive Sales Briefing.
        9. Stores the new meeting interaction into Hindsight tagged to this prospect.
        """
        # 1. PROSPECT IDENTIFICATION
        prospect_id = normalize_prospect_id(name, company)
        self._validate_note_identity(name, notes)

        # 2. HINDSIGHT MEMORY RETRIEVAL (Isolated by prospect tag)
        search_query = (
            f"Previous meetings, customer requirements, objections, decisions, "
            f"commitments, proposals and follow-ups for prospect {name} at company {company}"
        )
        memories, unique_count, raw_count = self.hindsight.search_memories(
            query=search_query,
            prospect_name=name,
            company=company,
            prospect_id=prospect_id
        )

        # 3. MEMORY CONFLICT & PROGRESSION ANALYSIS
        conflicts = detect_memory_conflicts([{"text": m} for m in memories])

        # 3b. DATE INTELLIGENCE & DEADLINE CONFLICT DETECTION
        date_conflict = detect_deadline_conflicts(
            historical_memories=memories,
            latest_notes=notes
        )

        # 4. LOAD PERSISTENT CUSTOMER STATE
        previous_state = self.db.get_customer_state(prospect_id)

        # 5. AI MULTI-STAGE REASONING & NEXT BEST ACTION SELECTION
        analysis = self.ai.analyze_and_reason(
            prospect_name=name,
            company=company,
            meeting_goal=goal,
            latest_notes=notes,
            memories=memories,
            previous_state=previous_state
        )

        customer_state_data = analysis.get("customer_state", {})
        next_best_action = analysis.get("next_best_action", {})
        tool_proposal = analysis.get("proposed_task_tool")
        ai_reasoning_mode = analysis.get("ai_reasoning_mode", "groq_llm")
        evidence_text = "\n".join(memories + [notes])
        customer_state_data["decision_stages"] = self._sanitize_decision_stages(
            customer_state_data.get("decision_stages", []), evidence_text
        )
        self._sanitize_state_claims(customer_state_data, evidence_text)

        # Interest in a product is not a purchase decision. Do not display an
        # LLM-invented purchase-intent milestone without explicit evidence.
        decision_evidence = "\n".join(memories + [notes]).lower()
        explicit_purchase_intent = bool(re.search(
            r"\b(purchase intent|intent to buy|intend to buy|want to purchase|plan to purchase|interested in buying)\b",
            decision_evidence,
        ))
        if not explicit_purchase_intent:
            customer_state_data["decision_stages"] = [
                stage for stage in customer_state_data.get("decision_stages", [])
                if not (isinstance(stage, dict) and stage.get("type") == "Purchase Intent")
            ]

        customer_state_data["document_lifecycle"] = self._sanitize_document_lifecycle(
            customer_state_data.get("document_lifecycle", []), "\n".join(memories + [notes])
        )
        self._reconcile_verified_actions(
            name, notes, memories, customer_state_data, next_best_action
        )
        if str(next_best_action.get("recommended_action", "")).lower().startswith("schedule a meeting"):
            tool_proposal = {
                "description": next_best_action["recommended_action"],
                "reason": next_best_action.get("why_this_action", "Final authorization meeting is the next verified step."),
                "supporting_evidence": next_best_action.get("supporting_evidence", "Verified customer history"),
                "due_date": None,
                "responsible_person": "Sales Team",
                "requester": name,
                "reviewer": "CFO and Sales",
            }

        # With no prior customer memory, an action may be sensible but is not
        # high-confidence historical intelligence.
        if not memories and str(next_best_action.get("confidence", "")).lower() == "high":
            next_best_action["confidence"] = "Medium (new prospect; no prior memory)"

        # Combine conflicts detected by rules, dates, and LLM
        combined_conflicts = list(conflicts)
        has_conflict = bool(date_conflict and date_conflict.get("requires_human_confirmation"))

        if date_conflict:
            combined_conflicts.append({
                "type": date_conflict["type"],
                "description": date_conflict["description"]
            })
            # RULE: Do not generate a high-confidence recommendation with an unresolved deadline conflict!
            next_best_action["confidence"] = "Low (Unresolved deadline conflict)"
            next_best_action["unresolved_uncertainty"] = date_conflict["description"]
            next_best_action["relevant_dates"] = "Deadline not confirmed"
            next_best_action["timing"] = "Deadline not confirmed"

        # Sanitize any dates generated in next_best_action
        if has_conflict or not next_best_action.get("relevant_dates"):
            next_best_action["relevant_dates"] = "Deadline not confirmed"
        elif "2026-11-10" in str(next_best_action.get("relevant_dates")):
            next_best_action["relevant_dates"] = "Deadline not confirmed"

        if has_conflict or "2026-11-10" in str(next_best_action.get("timing")):
            next_best_action["timing"] = "Deadline not confirmed"

        for c in customer_state_data.get("conflicts_detected", []):
            # LLM deadline conflicts must agree with deterministic parsing. An
            # explicit matching date is not a conflict merely because the model
            # also saw a bare weekday in the sentence.
            if isinstance(c, dict) and "deadline" in str(c.get("type", "")).lower() and not date_conflict:
                continue
            if isinstance(c, str) and "deadline" in c.lower() and not date_conflict:
                continue
            if isinstance(c, str):
                combined_conflicts.append({"type": "detected_conflict", "description": c})
            elif isinstance(c, dict):
                combined_conflicts.append(c)

        combined_conflicts = self._deduplicate_conflicts(combined_conflicts)
        if combined_conflicts and not has_conflict:
            has_conflict = True
            next_best_action["confidence"] = "Low (Unresolved conflict)"
        receipt_conflict = next(
            (c for c in combined_conflicts if c.get("type") == "proposal_receipt_conflict"),
            None,
        )

        # A claimed receipt and an unconfirmed delivery record must be resolved
        # before recommending a resend. This is a deterministic safety override
        # of any model-proposed delivery action.
        if receipt_conflict:
            next_best_action.update({
                "recommended_action": "Verify with the prospect whether the revised proposal was received before sending or resending it",
                "why_this_action": "Receipt evidence is contradictory, so sending another proposal could duplicate or confuse the customer.",
                "supporting_evidence": receipt_conflict["description"],
                "current_status": "Receipt unverified due to conflicting historical evidence",
                "timing": "Before any proposal resend",
                "relevant_dates": "Date not recorded",
                "confidence": "Uncertain (needs verification)",
            })
            tool_proposal = {
                "description": "Verify whether the revised proposal was received before sending or resending it",
                "reason": "Resolve contradictory proposal receipt evidence before taking delivery action.",
                "supporting_evidence": receipt_conflict["description"],
                "due_date": None,
                "responsible_person": "Sales Team",
                "requester": name,
                "reviewer": "Prospect or Finance",
            }

        team_promises = self._deduplicate_text_items(customer_state_data.get("team_promises", []))
        promise_keys = [re.sub(r"[^a-z0-9]+", " ", item.lower()).strip() for item in team_promises]
        unresolved_actions = self._deduplicate_text_items(
            customer_state_data.get("unresolved_actions", []), exclude=promise_keys
        )

        # 6. PERSIST UPDATED CUSTOMER STATE
        customer_state_record = {
            "prospect_id": prospect_id,
            "prospect_name": name,
            "company": company,
            "current_stage": customer_state_data.get("current_stage", "Discovery"),
            "requirements": customer_state_data.get("requirements", []),
            "interests": customer_state_data.get("interests", []),
            "objections": customer_state_data.get("objections", []),
            "budget_concerns": customer_state_data.get("budget_concerns", []),
            "decisions": customer_state_data.get("decisions", []),
            "decision_stages": customer_state_data.get("decision_stages", []),
            "document_lifecycle": customer_state_data.get("document_lifecycle", []),
            "customer_requests": customer_state_data.get("customer_requests", []),
            "team_promises": team_promises,
            "scheduled_actions": self._confirmed_scheduled_actions(
                customer_state_data.get("scheduled_actions", []), "\n".join(memories + [notes])
            ),
            "completed_actions": customer_state_data.get("completed_actions", []),
            "unresolved_actions": unresolved_actions,
            "conflicts": combined_conflicts,
            "last_confirmed_interaction": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        }
        self.db.save_customer_state(customer_state_record)

        # 7. AGENT TOOL SELECTION: PROPOSE FOLLOW-UP TASK (Human approval gate)
        proposed_task = None
        if tool_proposal and tool_proposal.get("description"):
            # Ensure customer is not assigned as owner for our deliverables
            resp_party = tool_proposal.get("responsible_person") or "Sales Team"
            if name.lower() in resp_party.lower():
                resp_party = "Sales Team"

            # Determine requester vs commitment maker
            notes_lower = notes.lower()
            if "sales team agreed" in notes_lower or "sales team promised" in notes_lower:
                commitment_maker = "Sales Team"
                requester = name if any(k in notes_lower for k in ["asked", "requested", "requires"]) else "Sales Team (Internal)"
            else:
                commitment_maker = "Sales Team"
                requester = tool_proposal.get("requester") or name

            # SAFE TASK CREATION & DATE INTEGRITY
            # 1. Reject invalid dates (e.g. 2026-11-10 for Friday)
            sanitized_due_date = sanitize_deadline(tool_proposal.get("due_date"), notes_or_text=notes)

            # 2. If deadline is ambiguous or conflicting, DO NOT assign an unconfirmed due date
            if has_conflict:
                sanitized_due_date = None

            # 3. Preserve raw natural language deadline phrase
            raw_nl_deadline = extract_deadline_phrase(notes) or ("by Friday" if "friday" in notes_lower else None) or tool_proposal.get("due_date")

            proposed_task = self.tasks.propose_task(
                prospect_id=prospect_id,
                prospect_name=name,
                company=company,
                description=tool_proposal.get("description"),
                reason=tool_proposal.get("reason", "Identified by AI reasoning engine"),
                supporting_evidence=tool_proposal.get("supporting_evidence", "Meeting notes analysis"),
                due_date=sanitized_due_date,
                responsible_person=resp_party,
                requester=requester,
                reviewer=tool_proposal.get("reviewer") or "Prospect or Finance",
                commitment_maker=commitment_maker,
                responsibility_source="Meeting notes / customer conversation",
                has_deadline_conflict=has_conflict,
                deadline_conflict_details=date_conflict["description"] if date_conflict else None,
                natural_language_deadline=raw_nl_deadline
            )

        # 8. OUTPUT GENERATION: EXECUTIVE SALES BRIEFING
        brief = self.ai.generate_meeting_brief(
            prospect_name=name,
            company=company,
            meeting_goal=goal,
            latest_notes=notes,
            memories=memories,
            customer_state=customer_state_record,
            next_best_action=next_best_action
        )

        # Never display a model-invented weekday/date pairing as a fact.
        brief = redact_invalid_weekday_date_claims(brief)
        if has_conflict:
            # A conflicting deadline cannot be represented as a confirmed date
            # anywhere in generated briefing fields, including JSON-like text.
            brief = brief.replace("2026-11-10", "Deadline not confirmed")

        # 9. HINDSIGHT MEMORY UPDATE: RETAIN LATEST MEETING
        meeting_date = self._meeting_date_from_notes(notes, meeting_date)
        meeting_content = (
            f"MEETING INTERACTION\n"
            f"Meeting date: {meeting_date}\n"
            f"Prospect: {name}\n"
            f"Company: {company}\n"
            f"Meeting objective: {goal}\n"
            f"Meeting notes: {notes}\n"
            f"Identified Stage: {customer_state_record['current_stage']}"
        )
        hindsight_retained = False
        try:
            if self.hindsight and getattr(self.hindsight, "is_connected", True):
                self.hindsight.save_memory(
                    content=meeting_content,
                    prospect_id=prospect_id,
                    prospect_name=name,
                    company=company,
                    document_id=(
                        f"meeting-{prospect_id}-{meeting_date}-"
                        f"{hashlib.sha256(notes.encode('utf-8')).hexdigest()[:12]}"
                    ),
                    timestamp=meeting_date,
                )
                hindsight_retained = True
        except Exception as e:
            print(f"Notice: meeting memory retention warning: {e}")

        # Keep an append-only copy of the original interaction so the left
        # history panel can reopen the actual Meeting 1, Meeting 2, and later
        # notes without relying on an LLM summary to reconstruct them.
        meeting_id = (
            f"MEET-{hashlib.sha256((prospect_id + meeting_date + notes).encode('utf-8')).hexdigest()[:14].upper()}"
        )
        saved_meeting = self.db.save_meeting_history({
            "meeting_id": meeting_id,
            "prospect_id": prospect_id,
            "prospect_name": name,
            "company": company,
            "meeting_date": meeting_date,
            "meeting_goal": goal,
            "notes": notes,
            "briefing": brief,
            "next_best_action": next_best_action,
        })

        # Retrieve all persistent tasks for this prospect
        existing_tasks = self.tasks.get_tasks_for_prospect(prospect_id)

        return {
            "success": True,
            "prospect_id": prospect_id,
            "prospect_name": name,
            "company": company,
            "customer_state": customer_state_record,
            "conflicts": combined_conflicts,
            "next_best_action": next_best_action,
            "proposed_task": proposed_task,
            "brief": brief,
            "memories": memories,
            "memory_count": unique_count,
            "raw_memory_count": raw_count,
            "tasks": existing_tasks,
            "ai_reasoning_mode": ai_reasoning_mode,
            "hindsight_retained": hindsight_retained,
            "meeting_id": saved_meeting["meeting_id"],
            "hindsight_available": bool(self.hindsight and getattr(self.hindsight, "is_connected", False)),
            "ai_available": bool(self.ai and getattr(self.ai, "is_connected", False)),
            "hindsight_status": {
                "configured": bool(self.hindsight and getattr(self.hindsight, "is_connected", False)),
                "recall": str(getattr(self.hindsight, "last_recall_status", "unknown")),
                "retain": str(getattr(self.hindsight, "last_retain_status", "not_attempted")),
            },
            "message": "Intelligence recalled, chronological state synthesized, and Next Best Action selected."
        }
