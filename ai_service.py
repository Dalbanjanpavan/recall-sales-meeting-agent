"""
AI Reasoning and Briefing Service for Recall — Sales Meeting Intelligence Agent.
Implements multi-stage reasoning using Groq API:
- Stage A: Memory understanding & factual extraction
- Stage B: Chronological customer-state & decision stage analysis
- Stage C: Proposal & document lifecycle tracking
- Stage D: Problem identification & conflict resolution
- Stage E: Next Best Action selection with evidence citation
- Stage F: Tool call proposal (Follow-up Task) with requester/owner separation
- Stage G: Executive Meeting Briefing generation
"""

import os
import re
import json
import logging
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from groq import Groq
from date_validator import get_current_kolkata_time, redact_invalid_weekday_date_claims

load_dotenv()

logger = logging.getLogger(__name__)


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Robustly extract and parse JSON object from text or markdown code fence."""
    if not text:
        return None

    # Try matching ```json ... ``` block
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    candidate = match.group(1) if match else text

    # Find outer curly braces
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = candidate[start:end + 1]
        try:
            return json.loads(json_str)
        except Exception:
            # Clean common trailing commas
            cleaned = re.sub(r",\s*([\]}])", r"\1", json_str)
            try:
                return json.loads(cleaned)
            except Exception as e:
                logger.warning(f"Failed to parse cleaned JSON: {e}")

    return None


class AIService:
    """Provides LLM-powered chronological analysis, reasoning, action selection, and briefing."""

    def __init__(self, client: Optional[Groq] = None, model: Optional[str] = None, offline_mode: bool = False):
        self.is_connected = False
        if offline_mode:
            self.client = None
            return

        if client:
            self.client = client
            self.is_connected = True
        else:
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                logger.warning("GROQ_API_KEY missing in .env. Service will operate in rule-based fallback mode.")
                self.client = None
                return
            try:
                self.client = Groq(api_key=api_key)
                self.is_connected = True
            except Exception as e:
                logger.warning(f"Failed to initialize Groq client: {e}")
                self.client = None

        self.model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

    def analyze_and_reason(
        self,
        prospect_name: str,
        company: str,
        meeting_goal: str,
        latest_notes: str,
        memories: List[str],
        previous_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Executes Multi-Stage Reasoning:
        Extracts verified customer state, decision stages, document lifecycle,
        identifies unresolved commitments, evaluates Next Best Action, and prepares tool proposal.
        """
        if not self.client:
            logger.info("Groq client not available; running deterministic fallback reasoning.")
            fallback = self._build_fallback_reasoning(
                prospect_name=prospect_name,
                company=company,
                meeting_goal=meeting_goal,
                latest_notes=latest_notes,
                memories=memories,
                previous_state=previous_state
            )
            fallback["ai_reasoning_mode"] = "fallback_deterministic"
            return fallback

        memory_context = "\n".join(
            f"[{idx + 1}] {mem}" for idx, mem in enumerate(memories)
        ) if memories else "No previous meeting memories retrieved from Hindsight."

        prev_state_str = json.dumps(previous_state, indent=2) if previous_state else "None recorded (New prospect interaction)."
        reference_datetime = get_current_kolkata_time()
        reference_date_text = f"{reference_datetime.date().isoformat()} ({reference_datetime.strftime('%A')})"

        prompt = f"""
You are the Reasoning Engine for Recall, an AI Sales Meeting Intelligence Agent.

Analyze the prospect's evolving situation across historical memories and latest meeting notes.
You must output a single, strictly valid JSON object.

PROSPECT: {prospect_name}
COMPANY: {company}
UPCOMING MEETING OBJECTIVE: {meeting_goal}

PREVIOUSLY SAVED STRUCTURED STATE:
{prev_state_str}

RETRIEVED HINDSIGHT HISTORICAL MEMORIES:
{memory_context}

LATEST MEETING NOTES:
{latest_notes}

CRITICAL REASONING & ACCURACY RULES:

1. CUSTOMER DECISION ACCURACY (Never treat one as another!):
   - "Initial Pricing Approval": Finance or prospect approved initial pricing/quote. (Does NOT mean purchase is approved!)
   - "Conditional Approval": Approved ONLY subject to explicit conditions. (Must explicitly record and retain conditions!)
   - "Purchase Intent": Prospect expressed desire or intent to buy. (Does NOT mean contract is signed!)
   - "Final Purchase Approval": Formal internal approval to purchase.
   - "Contract Signed": Legally binding contract executed. (Does NOT prove implementation or payment is complete!)
   - "Purchase Completed": Full procurement and implementation complete.
   Always prioritize the latest confirmed decision while preserving older decisions as history.

2. DOCUMENT & PROPOSAL LIFECYCLE (Never infer a later state from an earlier state!):
   Track separate states:
   - Requested: Customer asked for something.
   - Promised: Team promised to send/do it.
   - Scheduled: Explicit appointment/calendar activity confirmed.
   - Sent: Dispatched by team.
   - Delivered: Confirmed reached customer.
   - Received: Customer confirmed receipt.
   - Reviewed: Customer confirmed reviewing.
   - Approved: Formally approved.
   - Rejected: Formally rejected.
   (Promised ≠ scheduled ≠ sent ≠ delivered ≠ received ≠ reviewed ≠ approved).

3. RESPONSIBILITY AND COMMITMENT ACCURACY:
   - For every action, distinguish:
     * Requester: Who asked for it.
     * Responsible Party: Who is responsible to complete it (e.g. Sales Team). NEVER assign the customer as the task owner for our deliverables!
     * Reviewer: Who reviews/approves it (e.g. Finance manager, Prospect).
   - If responsible person is unknown, write "Not recorded".

4. DATE & DEADLINE ACCURACY:
   - Current meeting date reference is {reference_date_text}.
   - Do not calculate a calendar date from a weekday. Repeat only an explicit date supplied in evidence; otherwise use "Deadline not confirmed".
   - Do not calculate a calendar date from a weekday. Repeat only an explicit date supplied in evidence; otherwise use "Deadline not confirmed".
   - If a deadline is conflicting or ambiguous (e.g. historical Nov 13 vs note "by Friday"), do NOT assign a confirmed calendar date. Set "due_date": null, "relevant_dates": "Deadline not confirmed", and list the conflict in "conflicts_detected".
   - A deadline passing does NOT mean an action was completed.
   - Internal task completion does NOT advance document lifecycle states (Sent, Delivered, Received) without explicit factual transmission evidence.
   - Never invent dates, deadlines, or people.

5. NEXT BEST ACTION & TOOL SELECTION:
   - Recommend ONE primary actionable step based on latest confirmed state.
   - Do NOT recommend actions that are already confirmed completed, rejected, or superseded.
   - If there is no meaningful actionable next step, set "proposed_task_tool" to null.
   - State uncertainty when evidence is incomplete.

Respond with ONLY a JSON object with this exact structure:
{{
  "customer_state": {{
    "current_stage": "Discovery | Demonstration | Pricing / Proposal | Negotiation | Contract Review | Closed Won",
    "requirements": ["list of confirmed prospect requirements"],
    "interests": ["list of confirmed interests"],
    "objections": ["active unaddressed objections or concerns"],
    "budget_concerns": ["confirmed budget or pricing concerns"],
    "decisions": ["confirmed decisions made by prospect"],
    "decision_stages": [
      {{
        "type": "Initial Pricing Approval | Conditional Approval | Purchase Intent | Final Purchase Approval | Contract Signed | Purchase Completed",
        "status": "Confirmed | Pending | Rejected",
        "conditions": "Specific conditions if conditional approval, else 'None'",
        "source": "Source citation with date"
      }}
    ],
    "document_lifecycle": [
      {{
        "document": "Name of document or proposal",
        "state": "Requested | Promised | Scheduled | Sent | Delivered | Received | Reviewed | Approved | Rejected",
        "source": "Source citation with date",
        "responsible": "Sales Team | Specific Name | Not recorded"
      }}
    ],
    "customer_requests": ["confirmed requests made by customer"],
    "team_promises": ["confirmed commitments made by our team"],
    "scheduled_actions": ["actions confirmed with an explicit scheduled date/time"],
    "completed_actions": ["actions confirmed completed"],
    "unresolved_actions": ["actions requested or promised that remain unconfirmed or incomplete"],
    "conflicts_detected": ["any conflicting facts between earlier and later interactions"]
  }},
  "next_best_action": {{
    "recommended_action": "Specific primary recommended next action",
    "why_this_action": "Strategic explanation of why this action is critical now",
    "supporting_evidence": "Explicit historical fact or note supporting this",
    "relevant_dates": "Explicit date or 'Date not recorded'",
    "current_status": "Current status of this item",
    "responsible_person": "Responsible person or 'Not recorded'",
    "timing": "Timing or 'Not recorded'",
    "confidence": "High | Medium | Uncertain (needs verification)",
    "if_already_completed": "Alternative guidance if the user already did this"
  }},
  "proposed_task_tool": {{
    "tool_name": "create_followup_task",
    "description": "Clear actionable description of the task to be created",
    "reason": "Why this task should be executed",
    "supporting_evidence": "Specific historical fact from memory or notes",
    "due_date": null,
    "responsible_person": "Sales Team",
    "requester": "{prospect_name}",
    "reviewer": "Prospect or Finance"
  }}
}}
"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are Recall's deterministic sales reasoning engine. You return only valid JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.1,
                max_completion_tokens=2500
            )

            raw_content = response.choices[0].message.content or ""
            parsed = extract_json_from_text(raw_content)

            if parsed and "customer_state" in parsed and "next_best_action" in parsed:
                # Add reasoning mode metadata
                parsed["ai_reasoning_mode"] = "groq_llm"
                return parsed

            logger.warning("Model returned JSON missing required keys, applying fallback structure.")

        except Exception as e:
            logger.error(f"Error in analyze_and_reason (falling back to deterministic logic): {e}")

        # Graceful deterministic fallback
        fallback = self._build_fallback_reasoning(
            prospect_name=prospect_name,
            company=company,
            meeting_goal=meeting_goal,
            latest_notes=latest_notes,
            memories=memories,
            previous_state=previous_state
        )
        fallback["ai_reasoning_mode"] = "fallback_deterministic"
        return fallback

    def _build_fallback_reasoning(
        self,
        prospect_name: str,
        company: str,
        meeting_goal: str,
        latest_notes: str,
        memories: List[str],
        previous_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Deterministic rule-based fallback when Groq LLM is unavailable or JSON fails."""
        combined_text = "\n".join(memories + [latest_notes]).lower()
        has_proposal = bool(re.search(r"\b(proposal|quotation)\b", combined_text))
        # A pricing proposal is not a pricing objection, and promising a demo
        # is not evidence that the demo has been completed.
        has_pricing = "pricing" in combined_text or "price" in combined_text
        has_demo = "demo" in combined_text or "demonstration" in combined_text
        pricing_concern = bool(re.search(
            r"(?:price|pricing|cost|budget)[^.\n]{0,60}(?:too (?:high|expensive)|concern|objection|unaffordable|discount|reduce|constraint)|(?:too (?:high|expensive)|concern|objection|unaffordable|discount|reduce|constraint)[^.\n]{0,60}(?:price|pricing|cost|budget)",
            combined_text,
        ))
        demo_completed = bool(re.search(
            r"(?:demo|demonstration)[^.\n]{0,60}(?:completed|conducted|presented|held|finished)|(?:completed|conducted|presented|held|finished)[^.\n]{0,60}(?:demo|demonstration)",
            combined_text,
        ))
        demo_promised = bool(re.search(
            r"(?:closeai|sales team|we)[^.\n]{0,100}(?:agreed|promised|will prepare|will provide|will conduct)[^.\n]{0,100}(?:demo|demonstration)",
            combined_text,
        ))
        has_contract_mention = any("contract" in m.lower() for m in memories) or "contract" in latest_notes.lower()
        contract_signed = any("contract signed" in m.lower() or "agreement signed" in m.lower() for m in memories) or ("contract signed" in latest_notes.lower() or "agreement signed" in latest_notes.lower())
        if "unsigned" in latest_notes.lower() or "not signed" in latest_notes.lower():
            contract_signed = False

        has_intent = bool(re.search(r"\b(purchase intent|intent to buy|intend to buy|want to purchase|plan to purchase|interested in buying)\b", combined_text))

        stage = "Discovery"
        if contract_signed:
            stage = "Contract Signed"
        elif has_intent or (has_contract_mention and not contract_signed):
            stage = "Contract Review"
        elif has_proposal or has_pricing:
            stage = "Pricing / Proposal"
        elif has_demo:
            stage = "Demonstration"

        # Lifecycle evidence must refer to the proposal/quotation itself. A
        # CFO reviewing an initial quote cannot advance a revised proposal.
        doc = r"(?:proposal|quotation)"
        def has_doc_event(event: str) -> bool:
            return bool(re.search(rf"\b{doc}\b[^.\n]{{0,24}}\b{event}\b|\b{event}\b(?:\s+the)?\s+(?:revised\s+)?\b{doc}\b", combined_text))

        proposal_requested = has_doc_event("requested|ask(?:ed)?|require(?:d)?")
        proposal_promised = has_doc_event("promised") or bool(re.search(rf"(?:closeai|sales team|we)\b[^.\n]{{0,80}}\b(promised|agreed|will send|will prepare|prepare)\b[^.\n]{{0,80}}\b{doc}\b", combined_text))
        proposal_sent = has_doc_event("sent|dispatched")
        proposal_delivered = has_doc_event("delivered")
        proposal_received = has_doc_event(r"received|receipt\s+(?:was\s+)?confirmed")
        proposal_reviewed = has_doc_event("reviewed")
        proposal_approved = has_doc_event("approved")

        # Decision classification
        decision_stages = []
        has_final_approval = any(k in latest_notes.lower() for k in ["final purchase approval", "final approval", "purchase approved"]) or any("final purchase approval" in m.lower() for m in memories)
        has_purchase_completed = any(k in latest_notes.lower() for k in ["purchase completed", "payment received", "deal closed won", "implementation underway"])

        if "conditional approval" in latest_notes.lower() or any("conditional" in m.lower() for m in memories):
            cond_text = "Retain specified conditions from customer discussion"
            for marker in ["provided", "subject to", "conditional on", "contingent upon", "condition:"]:
                if marker in latest_notes.lower():
                    m_idx = latest_notes.lower().index(marker)
                    cond_text = latest_notes[m_idx:].strip(". \t\n")
                    break
            decision_stages.append({
                "type": "Conditional Approval",
                "status": "Confirmed",
                "conditions": cond_text,
                "source": "Latest notes / Hindsight memory"
            })
        elif has_purchase_completed:
            decision_stages.append({
                "type": "Purchase Completed",
                "status": "Confirmed",
                "conditions": "Transaction finalized and implementation initialized",
                "source": "Financial / implementation records"
            })
        elif contract_signed:
            decision_stages.append({
                "type": "Contract Signed",
                "status": "Confirmed",
                "conditions": "Formal agreement executed",
                "source": "Signed contract"
            })
        elif has_final_approval:
            decision_stages.append({
                "type": "Final Purchase Approval",
                "status": "Confirmed",
                "conditions": "Formal purchase approval granted by authorized stakeholder",
                "source": "Customer authorization notes"
            })
        elif has_intent and not contract_signed:
            decision_stages.append({
                "type": "Purchase Intent",
                "status": "Confirmed",
                "conditions": "Formal contract remains unsigned",
                "source": "Customer discussion notes"
            })
        elif proposal_approved:
            decision_stages.append({
                "type": "Initial Pricing Approval",
                "status": "Confirmed",
                "conditions": "Final purchase approval pending contract signing",
                "source": "Pricing proposal review"
            })

        # Document lifecycle
        doc_lifecycle = []
        if has_proposal:
            curr_doc_state = "Promised" if proposal_promised else "Requested" if proposal_requested else "Not confirmed"
            if proposal_approved:
                curr_doc_state = "Approved"
            elif proposal_reviewed:
                curr_doc_state = "Reviewed"
            elif proposal_received:
                curr_doc_state = "Received"
            elif proposal_delivered:
                curr_doc_state = "Delivered"
            elif proposal_sent:
                curr_doc_state = "Sent"
            doc_lifecycle.append({
                "document": "Revised Proposal / Quotation",
                "state": curr_doc_state,
                "source": "Meeting notes / Hindsight memory",
                "responsible": "Sales Team"
            })

        # Next Best Action Selection
        if proposal_sent or proposal_delivered:
            if not proposal_received:
                rec_action = f"Verify whether the proposal has been delivered to and received by {prospect_name}."
                reason = "The proposal was sent or delivered, but receipt is not confirmed."
                evidence = "Document lifecycle has sending or delivery evidence without confirmed receipt."
                timing = "Next conversation"
                status = "Sent or delivered, receipt unconfirmed"
            elif not proposal_reviewed:
                rec_action = f"Confirm whether {prospect_name} and internal stakeholders have reviewed the delivered proposal."
                reason = "Receipt is confirmed, but customer review has not been verified."
                evidence = "Proposal receipt is recorded; review remains unconfirmed."
                timing = "Follow-up touchpoint"
                status = "Received, review unconfirmed"
            else:
                rec_action = f"Clarify the decision and remaining approval steps with {prospect_name}."
                reason = "The proposal was reviewed, but final approval remains unconfirmed."
                evidence = "Proposal review is confirmed without final purchase approval."
                timing = "Upcoming meeting"
                status = "Reviewed, approval unconfirmed"
        elif proposal_requested or proposal_promised:
            rec_action = f"Prepare and send the promised proposal or pricing details to {prospect_name}."
            reason = "The proposal was requested or promised, but sending or receipt has not been recorded."
            evidence = "Customer records contain a proposal request or Sales Team promise, without sending evidence."
            timing = "Next sales action"
            status = "Promised or requested; not sent"
        elif proposal_delivered and not proposal_reviewed:
            rec_action = f"Confirm whether {prospect_name} and internal stakeholders have reviewed the delivered proposal."
            reason = "Proposal delivery is confirmed, but customer review has not been verified."
            evidence = "Proposal was delivered; awaiting confirmed review."
            timing = "Follow-up touchpoint"
            status = "Delivered, review unconfirmed"
        elif has_intent and not contract_signed:
            rec_action = f"Clarify final contract requirements and procurement timeline with {prospect_name}."
            reason = "Purchase intent is established, but formal contract signing remains pending."
            evidence = "Customer expressed purchase intent without signed agreement."
            timing = "Upcoming meeting"
            status = "Intent confirmed, contract pending"
        elif pricing_concern:
            rec_action = f"Clarify pricing expectations and budget parameters with {prospect_name}."
            reason = "Pricing was identified as a focal area or concern."
            evidence = "Meeting objective or notes cite pricing discussions."
            timing = "Next meeting"
            status = "Status not confirmed"
        else:
            rec_action = f"Align on primary success criteria and next milestone for {meeting_goal}."
            reason = "Clear next steps are required to advance the opportunity."
            evidence = f"Upcoming meeting objective: {meeting_goal}"
            timing = "Next meeting"
            status = "Pending discovery"

        confirmed_meeting = not bool(re.search(r"\b(?:not|has not|hasn't|never)\s+(?:been\s+)?scheduled\b", combined_text)) and bool(re.search(
            r"\b(?:meeting|call)\b[^.\n]{0,50}\b(?:scheduled|confirmed|booked)\b|\b(?:scheduled|confirmed|booked)\b[^.\n]{0,50}\b(?:meeting|call)\b",
            combined_text,
        ))

        return {
            "customer_state": {
                "current_stage": stage,
                "requirements": [meeting_goal],
                "interests": ["Product demonstration and capabilities"],
                "objections": ["Pricing sensitivity"] if pricing_concern else [],
                "budget_concerns": ["Pricing review requested"] if pricing_concern else [],
                "decisions": [],
                "decision_stages": decision_stages,
                "document_lifecycle": doc_lifecycle,
                "customer_requests": [meeting_goal],
                "team_promises": ["Prepare the customized product demonstration and preliminary pricing proposal"] if demo_promised else [],
                "scheduled_actions": [f"Meeting on {meeting_goal}"] if confirmed_meeting else [],
                "completed_actions": ["Product demonstration"] if demo_completed else [],
                "unresolved_actions": [rec_action],
                "conflicts_detected": []
            },
            "next_best_action": {
                "recommended_action": rec_action,
                "why_this_action": reason,
                "supporting_evidence": evidence,
                "relevant_dates": "Date not recorded",
                "current_status": status,
                "responsible_person": "Sales Team",
                "timing": timing,
                "confidence": "Medium",
                "if_already_completed": "Confirm prospect's feedback and identify remaining evaluation hurdles."
            },
            "proposed_task_tool": {
                "tool_name": "create_followup_task",
                "description": rec_action,
                "reason": reason,
                "supporting_evidence": evidence,
                "due_date": None,
                "responsible_person": "Sales Team",
                "requester": prospect_name,
                "reviewer": "Prospect or Finance"
            }
        }

    def generate_meeting_brief(
        self,
        prospect_name: str,
        company: str,
        meeting_goal: str,
        latest_notes: str,
        memories: List[str],
        customer_state: Optional[Dict[str, Any]] = None,
        next_best_action: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Stage G: Generates the structured Executive Meeting Briefing.
        """
        if not self.client:
            return self._build_fallback_brief(
                prospect_name=prospect_name,
                company=company,
                meeting_goal=meeting_goal,
                latest_notes=latest_notes,
                customer_state=customer_state,
                next_best_action=next_best_action
            )

        memory_context = "\n".join(
            f"- {memory}" for memory in memories
        ) or "No previous meeting memories were retrieved."

        state_context = json.dumps(customer_state, indent=2) if customer_state else "Not available"
        nba_context = json.dumps(next_best_action, indent=2) if next_best_action else "Not available"
        reference_datetime = get_current_kolkata_time()
        reference_date_text = f"{reference_datetime.date().isoformat()} ({reference_datetime.strftime('%A')})"

        prompt = f"""
You are Recall, a reliable Sales Meeting Intelligence Agent.

Your task is to prepare a briefing for an upcoming sales meeting.

PROSPECT: {prospect_name}
COMPANY: {company}
UPCOMING MEETING OBJECTIVE: {meeting_goal}

PREVIOUS INFORMATION RETRIEVED FROM HINDSIGHT MEMORY:
{memory_context}

CURRENT CONFIRMED CUSTOMER STATE:
{state_context}

IDENTIFIED NEXT BEST ACTION:
{nba_context}

LATEST MEETING NOTES:
{latest_notes}

FACTUAL ACCURACY & DISTINCTION RULES:
1. Distinguish Decision Stages: Initial Pricing Approval ≠ Conditional Approval ≠ Purchase Intent ≠ Final Purchase Approval ≠ Contract Signed.
2. Distinguish Proposal Lifecycle: Requested ≠ Promised ≠ Scheduled ≠ Sent ≠ Delivered ≠ Received ≠ Reviewed ≠ Approved.
3. Distinguish Roles: Requester (customer) ≠ Responsible Party (sales team) ≠ Reviewer. Never assign the customer as the task owner for our commitments.
4. An expired deadline does NOT prove that an action was completed.
5. If information is missing, write "Not recorded".
6. Treat recommendations as suggestions, never as confirmed facts.
7. DATE & DEADLINE INTEGRITY: The reference meeting date is {reference_date_text}. Do not calculate calendar dates from weekday wording. If a deadline has a conflict or is unconfirmed, state 'Deadline not confirmed'.
8. TASK COMPLETION VS BUSINESS LIFECYCLE: Internal task completion does not automatically mark a proposal as sent, delivered or received. Require explicit confirmed evidence before stating that a document was delivered.

Prepare the briefing using these exact sections:

1. PROSPECT OVERVIEW
Summarise the prospect, company, current customer stage, and known requirements.

2. PREVIOUS DISCUSSIONS (CHRONOLOGICAL PROGRESSION)
Summarise chronological developments across previous interactions.

3. CUSTOMER CONCERNS & OBJECTIONS
List confirmed objections, questions, and unresolved concerns.

4. DECISIONS AND APPROVALS
List explicitly recorded decisions and approvals, distinguishing pricing approval, conditional approvals with conditions, and contract status.

5. COMMITMENTS AND FOLLOW-UPS
Present the information in a table with these columns:
Action | Category | Status | Requester | Responsible Party

6. LATEST MEETING UPDATE
Summarise what happened in the latest meeting notes.

7. RECOMMENDED DISCUSSION POINTS
Give practical suggestions for the upcoming meeting. Clearly label as recommendations.

8. QUESTIONS TO ASK
Suggest relevant questions based on known information.

9. NEXT BEST ACTION
State the primary recommended action with:
- Recommended Action:
- Why This Action:
- Supporting Evidence:
- Relevant Dates:
- Responsible Party:
- Timing:
- Confidence / Uncertainty:
"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are Recall, an accurate sales intelligence "
                            "assistant. Factual accuracy is more important "
                            "than producing a complete-looking answer."
                        )
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.2,
                max_completion_tokens=4000
            )

            brief = response.choices[0].message.content
            if brief and brief.strip():
                clean_brief = brief.strip()
                return redact_invalid_weekday_date_claims(clean_brief)

        except Exception as e:
            logger.error(f"Error in generate_meeting_brief: {e}")

        return self._build_fallback_brief(
            prospect_name=prospect_name,
            company=company,
            meeting_goal=meeting_goal,
            latest_notes=latest_notes,
            customer_state=customer_state,
            next_best_action=next_best_action
        )

    def _build_fallback_brief(
        self,
        prospect_name: str,
        company: str,
        meeting_goal: str,
        latest_notes: str,
        customer_state: Optional[Dict[str, Any]] = None,
        next_best_action: Optional[Dict[str, Any]] = None
    ) -> str:
        """Deterministic briefing when Groq is unavailable."""
        stage = (customer_state or {}).get("current_stage", "Discovery")
        nba = next_best_action or {}
        return (
            f"# Executive Sales Briefing: {prospect_name} at {company}\n\n"
            f"**Current Stage:** {stage}\n"
            f"**Upcoming Objective:** {meeting_goal}\n\n"
            f"### 1. Prospect Overview\n"
            f"Prospect: {prospect_name}\n"
            f"Company: {company}\n"
            f"Goal: {meeting_goal}\n\n"
            f"### 2. Latest Meeting Summary\n"
            f"{latest_notes}\n\n"
            f"### 3. Commitments & Status\n"
            f"| Action | Category | Status | Requester | Responsible Party |\n"
            f"|---|---|---|---|---|\n"
            f"| {meeting_goal} | Meeting Activity | Scheduled | {prospect_name} | Sales Team |\n\n"
            f"### 4. Next Best Action (Recommended)\n"
            f"- **Action:** {nba.get('recommended_action', 'Align on next milestones')}\n"
            f"- **Why This Action:** {nba.get('why_this_action', 'To maintain deal velocity')}\n"
            f"- **Supporting Evidence:** {nba.get('supporting_evidence', 'Meeting history')}\n"
            f"- **Status:** {nba.get('current_status', 'Pending')}\n"
            f"- **Responsible Party:** {nba.get('responsible_person', 'Sales Team')}\n"
        )
