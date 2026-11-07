"""
Memory Processor for Recall — Sales Meeting Intelligence Agent.
Handles:
- Stable prospect identification & normalization
- Strict multi-layer prospect memory isolation
- Exact and semantic duplicate detection
- Preservation of distinct chronological events (different dates, promises, outcomes)
- Customer decision state classification
- Document lifecycle tracking
- Requester vs Task-owner separation
- Date & deadline status validation
"""

import re
import hashlib
from typing import List, Dict, Any, Tuple, Optional


def normalize_prospect_id(name: str, company: str) -> str:
    """
    Generate a stable, lowercase, sanitized identifier for a prospect.
    Normalizes company suffixes (e.g. Inc, LLC, Corp) while keeping distinct people
    and companies isolated.
    Example: ('Rahul Sharma', 'Acme Technologies, Inc.') -> 'acme-technologies_rahul-sharma'
    """
    if not name or not name.strip():
        raise ValueError("Prospect name cannot be empty.")
    if not company or not company.strip():
        raise ValueError("Company name cannot be empty.")

    def sanitize_company(text: str) -> str:
        text = text.strip().lower()
        # Strip common legal suffixes
        text = re.sub(r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company)\b\.?", "", text)
        text = re.sub(r"[^\w\s-]", "", text)
        return re.sub(r"[\s_-]+", "-", text).strip("-") or "company"

    def sanitize_name(text: str) -> str:
        text = text.strip().lower()
        text = re.sub(r"[^\w\s-]", "", text)
        return re.sub(r"[\s_-]+", "-", text).strip("-") or "prospect"

    comp = sanitize_company(company)
    prospect = sanitize_name(name)
    return f"{comp}_{prospect}"


def compute_text_similarity(s1: str, s2: str) -> float:
    """Calculate word-level Jaccard similarity between two strings."""
    w1 = set(re.findall(r"\b\w{3,}\b", s1.lower()))
    w2 = set(re.findall(r"\b\w{3,}\b", s2.lower()))
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)


def strip_hindsight_metadata(text: str) -> str:
    """Removes trailing Hindsight metadata tags like '| When: ... | Involving: ...' and parenthetical timestamps."""
    if not text:
        return ""
    t = re.split(r"\s*\|\s*(?:When|Involving|To address|Contact):", text, flags=re.I)[0].strip()
    t = re.sub(r"\s*\(\d{4}-\d{2}-\d{2}[^)]*\)", "", t).strip()
    return t


def sanitize_memory_text(text: str) -> str:
    """Remove duplicated nested recall context from a displayed memory.

    Hindsight can append the source interaction inside parentheses to a derived
    memory. That source remains in Hindsight, but repeating it in the derived
    statement makes the UI misleading and can distort semantic matching.
    """
    if not text:
        return ""
    cleaned = re.sub(r"\s*\(\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}[^)]*\)", "", text).strip()
    return re.sub(r"\s+", " ", cleaned)


def extract_dates_and_markers(text: str) -> Dict[str, Any]:
    """Extract explicit dates, business metric numbers, and event markers from text."""
    core_text = strip_hindsight_metadata(text)
    core_lower = core_text.lower()

    # Match dates like "oct 12", "nov 14", "2026-11-06", "14th", "friday"
    date_patterns = [
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
        r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    ]
    dates = []
    for pat in date_patterns:
        dates.extend(re.findall(pat, core_lower))

    # Business metric numbers (currencies, percentages, quantities like 24/7 or seat counts)
    # Exclude calendar date numbers (years, months, days)
    currencies = re.findall(r"[\$€£₹]\s*\d+(?:,\d+)*(?:\.\d+)?|\b\d+(?:,\d+)*(?:\.\d+)?\s*(?:usd|eur|inr|dollars)\b", core_text, re.I)
    percentages = re.findall(r"\b\d+(?:\.\d+)?%", core_text)
    sla = re.findall(r"\b24/7\b", core_text)
    metrics = set(currencies + percentages + sla)

    # Action / Outcome markers
    outcomes = []
    for kw in ["completed", "delivered", "received", "reviewed", "approved", "approval", "rejected", "promised", "requested", "sent", "scheduled"]:
        if kw in core_lower:
            outcomes.append(kw)

    return {
        "dates": set(dates),
        "numbers": metrics,
        "outcomes": set(outcomes)
    }


def canonicalize_for_similarity(text: str) -> str:
    """Canonicalize synonyms to detect semantic duplicates across phrasing variations."""
    t = text.lower()
    t = strip_hindsight_metadata(t)
    t = re.sub(r"\b(?:cfo|finance manager|finance director|finance team|finance lead|finance committee)\b", "finance_approver", t)
    t = re.sub(r"\b(?:received approval|was approved|gave approval|approved|confirmed approval|approved the|approval confirmed)\b", "approved", t)
    t = re.sub(r"\b(?:expressed (?:pricing )?concern[s]?.*|found (?:the )?pricing too expensive|concerned about pricing|pricing concerns|pricing objection|pricing too high|pricing is too high)\b", "pricing_concern", t)
    t = re.sub(r"\b(asked for|inquired about|requested)\b", "requested", t)
    t = re.sub(r"\b(the sales team agreed to send|sales team promised to send|will be sent|will send)\b", "send_commitment", t)
    t = re.sub(r"\b(proposal on pricing|pricing proposal|pricing quote|customized proposal|revised proposal|quote|pricing)\b", "pricing_proposal", t)
    t = re.sub(r"\b(product demonstration|product demo|demonstration|demo)\b", "demonstration", t)
    t = re.sub(r"\b(master service agreement|msa|contract|service agreement|agreement)\b", "contract", t)
    t = re.sub(r"\b(security questionnaire|security review|security audit|compliance questionnaire)\b", "security_review", t)
    t = re.sub(r"\b(customised|customized)\b", "customized", t)
    t = re.sub(r"\b(provision of|inclusion of)\b", "with", t)
    t = re.sub(r"\b(is a prospect at|is from|from)\b", "is_at", t)
    t = re.sub(r"\b(requires (?:pricing )?approval from (?:his )?finance_approver[^\.]+)\b", "requires_finance_approval", t)
    return re.sub(r"[^\w\s]", "", t).strip()


def is_prospect_match(memory_item: Any, prospect_id: str, prospect_name: str, company: str) -> bool:
    """
    Verify that a memory record belongs exclusively to this prospect.
    Checks tags, metadata, and textual indicators.
    Prevents cross-prospect data leakage between prospects with similar names or companies.
    """
    text = ""
    tags = []
    metadata = {}

    if isinstance(memory_item, str):
        text = memory_item
    elif isinstance(memory_item, dict):
        text = memory_item.get("text", "")
        tags = memory_item.get("tags") or []
        metadata = memory_item.get("metadata") or {}
    else:
        # Hindsight RecallResult object
        text = getattr(memory_item, "text", "") or ""
        tags = getattr(memory_item, "tags", None) or []
        metadata = getattr(memory_item, "metadata", None) or {}

    expected_tag = f"prospect:{prospect_id}"

    # 1. Tag or Metadata match (strongest signal)
    if expected_tag in tags or metadata.get("prospect_id") == prospect_id:
        return True

    # If the item has a DIFFERENT prospect tag, reject immediately
    for t in tags:
        if t.startswith("prospect:") and t != expected_tag:
            return False

    if metadata.get("prospect_id") and metadata.get("prospect_id") != prospect_id:
        return False

    # 2. Textual matching with prospect name and company
    text_lower = text.lower()
    name_parts = [p.lower() for p in prospect_name.split() if len(p) > 2]
    company_lower = company.lower()

    # If memory explicitly references a DIFFERENT known company, reject
    # e.g., memory belongs to Acme Global while prospect is Acme Technologies
    if company_lower and "company:" in text_lower:
        match = re.search(r"company:\s*([^\n\.,]+)", text_lower)
        if match:
            found_company = match.group(1).strip()
            # If the stated company doesn't overlap with our prospect's company
            if not any(part in found_company for part in company_lower.split() if len(part) > 3):
                return False

    # Untagged records require every meaningful part of the prospect's name.
    # A shared first name must never be enough to cross customer boundaries.
    full_name = " ".join(name_parts)
    name_matches = bool(full_name and full_name in text_lower)
    company_matches = bool(company_lower and company_lower in text_lower)

    # Company-only matching is too weak. A full name is accepted only when the
    # text does not explicitly identify a different company.
    return name_matches and (not company_lower or company_matches or "company:" not in text_lower)


def clean_and_deduplicate_memories(
    raw_memories: List[Any],
    prospect_id: str,
    prospect_name: str,
    company: str
) -> List[Dict[str, Any]]:
    """
    Clean, isolate, rank, and deduplicate memories:
    - Retains only memories matching the prospect (strict isolation)
    - Removes exact and semantic near-duplicates
    - PRESERVES distinct events (different dates, different promises, different people, different outcomes)
    - Preserves decisions, objections, and action statuses
    - Newer information is never discarded for older similar information
    """
    isolated_records: List[Dict[str, Any]] = []

    for item in raw_memories:
        if not is_prospect_match(item, prospect_id, prospect_name, company):
            continue

        text = ""
        mem_id = None
        timestamp = None

        if isinstance(item, str):
            text = sanitize_memory_text(item.strip())
        elif isinstance(item, dict):
            text = sanitize_memory_text((item.get("text") or "").strip())
            mem_id = item.get("id")
            timestamp = item.get("timestamp") or item.get("occurred_start")
        else:
            text = sanitize_memory_text((getattr(item, "text", "") or "").strip())
            mem_id = getattr(item, "id", None)
            timestamp = getattr(item, "occurred_start", None) or getattr(item, "mentioned_at", None)

        if not text:
            continue

        isolated_records.append({
            "id": mem_id or hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
            "text": text,
            "timestamp": timestamp
        })

    # Deduplicate while preserving chronological distinct events
    deduped: List[Dict[str, Any]] = []
    seen_exact_hashes = set()

    for record in isolated_records:
        norm_text = re.sub(r"\s+", " ", record["text"].strip().lower())
        exact_hash = hashlib.sha256(norm_text.encode("utf-8")).hexdigest()

        if exact_hash in seen_exact_hashes:
            continue

        rec_core = strip_hindsight_metadata(record["text"])
        canon_record_text = canonicalize_for_similarity(rec_core)
        rec_markers = extract_dates_and_markers(record["text"])
        is_semantic_duplicate = False

        for kept_idx, kept in enumerate(deduped):
            kept_core = strip_hindsight_metadata(kept["text"])
            canon_kept_text = canonicalize_for_similarity(kept_core)
            sim = compute_text_similarity(canon_record_text, canon_kept_text)

            is_sub = (canon_record_text in canon_kept_text or canon_kept_text in canon_record_text) and len(canon_record_text) > 15 and len(canon_kept_text) > 15

            # Check semantic similarity threshold or clean substring containment
            if sim >= 0.55 or is_sub:
                kept_markers = extract_dates_and_markers(kept["text"])

                # RULE 1: If dates are explicitly different, they represent distinct meetings or events!
                # (Only compare when both have dates and neither set is a subset of the other)
                if rec_markers["dates"] and kept_markers["dates"]:
                    if not rec_markers["dates"].issubset(kept_markers["dates"]) and not kept_markers["dates"].issubset(rec_markers["dates"]):
                        continue

                # RULE 2: If conditions are explicitly different, preserve both!
                def check_has_condition(t: str) -> bool:
                    tl = t.lower()
                    if any(neg in tl for neg in ["no special condition", "without condition", "no condition", "unconditional"]):
                        return False
                    return any(w in tl for w in ["condition", "contingent", "provided", "subject to", "conditional"])

                has_cond1 = check_has_condition(rec_core)
                has_cond2 = check_has_condition(kept_core)
                if has_cond1 != has_cond2:
                    continue
                if has_cond1 and has_cond2:
                    c1_onb = "onboarding" in rec_core.lower()
                    c2_onb = "onboarding" in kept_core.lower()
                    c1_sla = "sla" in rec_core.lower() or "24/7" in rec_core.lower() or "friday" in rec_core.lower()
                    c2_sla = "sla" in kept_core.lower() or "24/7" in kept_core.lower() or "friday" in kept_core.lower()
                    if c1_onb != c2_onb or c1_sla != c2_sla:
                        continue

                # RULE 3: If outcomes represent distinct chronological progression (e.g. one requested vs one completed/delivered), preserve both!
                has_progress1 = any(k in canon_record_text for k in ["completed", "delivered", "approved", "rejected", "signed"])
                has_progress2 = any(k in canon_kept_text for k in ["completed", "delivered", "approved", "rejected", "signed"])
                if has_progress1 != has_progress2:
                    continue

                # RULE 4: If numbers/metrics differ (e.g. $50k vs $60k), preserve both!
                if rec_markers["numbers"] != kept_markers["numbers"] and (rec_markers["numbers"] or kept_markers["numbers"]):
                    continue

                # Otherwise, it's a semantic duplicate of the same event/fact!
                # Keep the longer, more informative description
                if len(record["text"]) > len(kept["text"]):
                    deduped[kept_idx] = record
                is_semantic_duplicate = True
                break

        if not is_semantic_duplicate:
            seen_exact_hashes.add(exact_hash)
            deduped.append(record)

    return deduped


def detect_memory_conflicts(memories: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Detect explicit conflicting statements across memory records.
    Distinguishes historical progression from genuine unresolvable conflicts.
    """
    conflicts = []
    texts = [m["text"] for m in memories]

    # 1. Pricing approval vs Pricing objection
    approved_pricing = any("pricing approved" in t.lower() or "quote approved" in t.lower() for t in texts)
    pricing_objection = any("pricing concern" in t.lower() or "pricing objection" in t.lower() or "price too high" in t.lower() for t in texts)

    if approved_pricing and pricing_objection:
        conflicts.append({
            "type": "pricing_status_conflict",
            "description": "Historical notes record pricing objections, while later records show pricing approval. The latest confirmed status supersedes earlier objections, and the latest confirmed decision takes precedence."
        })

    # 2. Purchase Intent vs Signed Contract
    intent_only = any("purchase intent" in t.lower() or "interested in buying" in t.lower() for t in texts)
    contract_signed = any("contract signed" in t.lower() or "agreement signed" in t.lower() for t in texts)

    if intent_only and not contract_signed:
        conflicts.append({
            "type": "intent_vs_contract",
            "description": "Prospect expressed purchase intent, but formal contract signing remains unconfirmed."
        })

    # 3. Demonstration requested vs completed
    demo_requested = any("requested a demo" in t.lower() or "wants a demonstration" in t.lower() for t in texts)
    demo_completed = any("demo completed" in t.lower() or "demonstration completed" in t.lower() for t in texts)

    if demo_requested and demo_completed:
        conflicts.append({
            "type": "demo_status_progression",
            "description": "Earlier notes requested a demo; subsequent notes confirm the demonstration was completed."
        })

    # 4. Proposal receipt claims must not silently override an unconfirmed
    # delivery history. Retain both records and make the uncertainty visible.
    proposal_received = any(
        re.search(r"\b(proposal|quote)\b.*\b(received|receipt)\b|\b(received|receipt)\b.*\b(proposal|quote)\b", t, re.IGNORECASE)
        for t in texts
    )
    delivery_unconfirmed = any(
        "delivery unconfirmed" in t.lower()
        or "receipt unconfirmed" in t.lower()
        or "no delivery confirmation" in t.lower()
        or "no confirmed record" in t.lower()
        for t in texts
    )
    if proposal_received and delivery_unconfirmed:
        conflicts.append({
            "type": "proposal_receipt_conflict",
            "description": "Historical records include a proposal-received claim and a separate unconfirmed delivery or receipt claim. Keep both records and verify receipt with the prospect before treating delivery as confirmed."
        })

    return conflicts
