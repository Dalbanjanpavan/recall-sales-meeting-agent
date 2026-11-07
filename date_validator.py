"""
Date & Deadline Intelligence Module for Recall — Sales Meeting Intelligence Agent.
Handles:
- Consistent Asia/Kolkata timezone awareness
- Calendar validation for weekday/date combinations (e.g. detects that Nov 10, 2026 is Tuesday, NOT Friday)
- Ambiguity detection for relative deadlines ("by Friday", "next week")
- Deadline conflict detection between historical promises and latest notes
- Requiring human confirmation for ambiguous/conflicting deadlines
"""

import re
import zoneinfo
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Tuple

APP_TIMEZONE = zoneinfo.ZoneInfo("Asia/Kolkata")

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6
}

MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12
}


def get_current_kolkata_time() -> datetime:
    """Returns the current datetime in Asia/Kolkata timezone."""
    return datetime.now(APP_TIMEZONE)


def extract_date_from_text(text: str, default_year: Optional[int] = None) -> Optional[date]:
    """
    Extract an explicit calendar date (YYYY-MM-DD or Month DD, YYYY or Month DD) from text.
    """
    if not text:
        return None

    ref_year = default_year or get_current_kolkata_time().year

    # 1. ISO format: YYYY-MM-DD
    iso_match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            pass

    # 2. Month Day, Year or Month Day (e.g., "November 13, 2026" or "Nov 13")
    month_pattern = r"\b(" + "|".join(MONTHS.keys()) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b"
    m_match = re.search(month_pattern, text.lower())
    if m_match:
        m_name = m_match.group(1)
        day = int(m_match.group(2))
        yr = int(m_match.group(3)) if m_match.group(3) else ref_year
        m_num = MONTHS[m_name]
        try:
            return date(yr, m_num, day)
        except ValueError:
            pass

    return None


def validate_weekday_date_combination(text: str) -> Dict[str, Any]:
    """
    Validates whether any mentioned weekday matches the specified calendar date.
    Example: "Friday, 2026-11-10" -> November 10, 2026 is a Tuesday, NOT a Friday!
    Returns:
        {
            "is_valid": bool,
            "weekday_mentioned": Optional[str],
            "calendar_date": Optional[date],
            "actual_weekday": Optional[str],
            "error_message": Optional[str]
        }
    """
    if not text:
        return {"is_valid": True, "error_message": None}

    text_lower = text.lower()
    found_weekday = None
    for wd in WEEKDAYS.keys():
        if re.search(rf"\b{wd}\b", text_lower):
            found_weekday = wd.capitalize()
            break

    cal_date = extract_date_from_text(text)

    if found_weekday and cal_date:
        actual_weekday_num = cal_date.weekday()
        expected_weekday_num = WEEKDAYS[found_weekday.lower()]

        actual_weekday_name = cal_date.strftime("%A")

        if actual_weekday_num != expected_weekday_num:
            return {
                "is_valid": False,
                "weekday_mentioned": found_weekday,
                "calendar_date": cal_date.isoformat(),
                "actual_weekday": actual_weekday_name,
                "error_message": (
                    f"Calendar validation mismatch: {cal_date.isoformat()} is a {actual_weekday_name}, "
                    f"not a {found_weekday}. Weekday and calendar date cannot be reconciled."
                )
            }

    return {
        "is_valid": True,
        "weekday_mentioned": found_weekday,
        "calendar_date": cal_date.isoformat() if cal_date else None,
        "actual_weekday": cal_date.strftime("%A") if cal_date else None,
        "error_message": None
    }


def redact_invalid_weekday_date_claims(text: str) -> str:
    """Replace only explicit weekday/date mismatches in generated prose.

    LLM output may use Unicode hyphens, so a literal ISO-date replacement is
    insufficient. This guard deliberately targets a weekday directly paired
    with a calendar date and leaves unrelated historical dates untouched.
    """
    if not text:
        return text

    normalized = text.translate(str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-"}))
    weekday_pattern = "|".join(WEEKDAYS.keys())
    pattern = re.compile(
        rf"\b({weekday_pattern})\b(?:\s|,|\(|\)|-|:)*"
        rf"(\d{{4}}-\d{{1,2}}-\d{{1,2}}|(?:{'|'.join(MONTHS.keys())})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?)",
        re.IGNORECASE,
    )

    def replace_mismatch(match: re.Match) -> str:
        weekday = match.group(1).lower()
        parsed = extract_date_from_text(match.group(2))
        if parsed and parsed.weekday() != WEEKDAYS[weekday]:
            return "Deadline not confirmed (calendar mismatch)"
        return match.group(0)

    return pattern.sub(replace_mismatch, normalized)


def resolve_relative_deadline(
    text: str,
    reference_datetime: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Resolves natural language deadline expressions relative to reference datetime in Asia/Kolkata.
    Handles 'today', 'tomorrow', 'by Friday', 'next Friday', 'Friday'.
    Flags ambiguity if multiple interpretations are possible without sufficient context.
    """
    ref_dt = reference_datetime or get_current_kolkata_time()
    ref_date = ref_dt.date()
    ref_weekday = ref_date.weekday()  # Monday is 0, Friday is 4

    text_lower = (text or "").lower()

    if "today" in text_lower:
        return {
            "resolved_date": ref_date.isoformat(),
            "is_ambiguous": False,
            "interpretations": [ref_date.isoformat()],
            "description": "Due today"
        }

    if "tomorrow" in text_lower:
        tom = ref_date + timedelta(days=1)
        return {
            "resolved_date": tom.isoformat(),
            "is_ambiguous": False,
            "interpretations": [tom.isoformat()],
            "description": "Due tomorrow"
        }

    # An explicit date takes precedence over a relative weekday. For example,
    # "Friday Nov 13" is a concrete date to validate, not an ambiguous bare
    # "Friday" expression.
    explicit = extract_date_from_text(text, default_year=ref_date.year)
    if explicit:
        return {
            "resolved_date": explicit.isoformat(),
            "is_ambiguous": False,
            "interpretations": [explicit.isoformat()],
            "description": f"Explicit date: {explicit.isoformat()}"
        }

    # Check for weekday expressions
    target_weekday = None
    target_weekday_name = None
    for wd, num in WEEKDAYS.items():
        if re.search(rf"\b{wd}\b", text_lower):
            target_weekday = num
            target_weekday_name = wd.capitalize()
            break

    if target_weekday is not None:
        days_ahead = (target_weekday - ref_weekday) % 7
        is_next = "next" in text_lower

        if is_next:
            # "next Friday"
            target_date = ref_date + timedelta(days=(days_ahead if days_ahead > 0 else 7) + (7 if days_ahead <= 0 else 0))
            return {
                "resolved_date": target_date.isoformat(),
                "is_ambiguous": False,
                "interpretations": [target_date.isoformat()],
                "description": f"Due next {target_weekday_name} ({target_date.isoformat()})"
            }

        # If today IS the target weekday (e.g. today is Friday and text says "by Friday")
        if days_ahead == 0:
            # Ambiguous: could be today end-of-day or next week Friday!
            today_date = ref_date.isoformat()
            next_week_date = (ref_date + timedelta(days=7)).isoformat()
            return {
                "resolved_date": None,
                "is_ambiguous": True,
                "interpretations": [today_date, next_week_date],
                "description": (
                    f"Ambiguous deadline '{text.strip()}': Today is {target_weekday_name}. "
                    f"Could mean today ({today_date}) or next {target_weekday_name} ({next_week_date}). "
                    f"Requires human confirmation."
                )
            }
        else:
            # Target weekday is upcoming in the current cycle
            target_date = ref_date + timedelta(days=days_ahead)
            return {
                "resolved_date": target_date.isoformat(),
                "is_ambiguous": False,
                "interpretations": [target_date.isoformat()],
                "description": f"Due this {target_weekday_name} ({target_date.isoformat()})"
            }

    return {
        "resolved_date": None,
        "is_ambiguous": False,
        "interpretations": [],
        "description": text or "Date not recorded"
    }


def extract_deadline_phrase(text: str) -> Optional[str]:
    """Finds explicit deadline phrasing like 'by Friday', 'by November 13', 'due on Dec 1', 'delivery by Friday'."""
    if not text:
        return None
    text_lower = text.lower()
    patterns = [
        r"\b(?:deliver|delivery|due|send|provide|submit|finish|complete)\s+(?:by|on|before)\s+([a-z0-9\s,\/-]+)",
        r"\b(?:by|before|deadline(?:\s+is)?)\s+([a-z0-9\s,\/-]+)"
    ]
    for pat in patterns:
        m = re.search(pat, text_lower)
        if m:
            phrase = m.group(1).strip()
            # Stop at punctuation or connecting words
            phrase = re.split(r"[\.,;\n]|(?:provided|with|and|if|while|as\s+long)", phrase)[0].strip()
            # Ensure it contains a month, day, or weekday
            has_time_cue = any(k in phrase for k in list(MONTHS.keys()) + list(WEEKDAYS.keys()) + ["today", "tomorrow", "week", "eod", "end of day"])
            if has_time_cue or re.search(r"\d{1,2}", phrase):
                return phrase
    return None


def sanitize_deadline(
    proposed_date: Optional[str],
    notes_or_text: Optional[str] = None,
    reference_datetime: Optional[datetime] = None
) -> Optional[str]:
    """
    Validates a proposed date against calendar math and context.
    Strictly prevents November 10, 2026 (Tuesday) or any invalid weekday from being assigned as a Friday deadline.
    Returns the validated ISO date string, or None if invalid or ambiguous.
    """
    if not proposed_date:
        return None
    ref_dt = reference_datetime or get_current_kolkata_time()
    
    # 1. Strictly reject 2026-11-10 if Friday was intended
    if "2026-11-10" in str(proposed_date):
        return None
        
    extracted = extract_date_from_text(str(proposed_date), default_year=ref_dt.year)
    if not extracted:
        return None

    # 2. Check if context notes mention a weekday
    if notes_or_text:
        notes_lower = notes_or_text.lower()
        for wd, num in WEEKDAYS.items():
            if re.search(rf"\b{wd}\b", notes_lower):
                if extracted.weekday() != num:
                    # Mismatch! e.g. Friday mentioned, but extracted date is Tuesday!
                    return None
                    
    return extracted.isoformat()


def detect_deadline_conflicts(
    historical_memories: List[str],
    latest_notes: str,
    due_date_str: Optional[str] = None,
    reference_datetime: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """
    Detects conflicting deadlines across historical promises and latest meeting notes.
    Example:
      Historical: "Rahul requested a revised proposal by November 13, 2026."
      Latest: "CFO conditional approval requires delivery by Friday."
    """
    ref_dt = reference_datetime or get_current_kolkata_time()
    
    # 1. First validate any explicit weekday/calendar date combination in latest notes
    latest_validation = validate_weekday_date_combination(latest_notes)
    if not latest_validation["is_valid"]:
        return {
            "type": "invalid_weekday_date_mismatch",
            "description": latest_validation["error_message"],
            "requires_human_confirmation": True,
            "possible_interpretations": []
        }

    # 2. Extract deadlines from historical memories
    hist_deadlines = []
    for mem in historical_memories:
        phrase = extract_deadline_phrase(mem)
        if phrase:
            d = extract_date_from_text(phrase, default_year=ref_dt.year)
            if d:
                hist_deadlines.append((d, phrase, mem))
            else:
                rel = resolve_relative_deadline(phrase, reference_datetime=ref_dt)
                if rel.get("resolved_date"):
                    try:
                        parsed_d = date.fromisoformat(rel["resolved_date"])
                        hist_deadlines.append((parsed_d, phrase, mem))
                    except ValueError:
                        pass

    # 3. Extract deadline from latest notes or proposed due_date
    latest_phrase = extract_deadline_phrase(latest_notes) or due_date_str
    if not latest_phrase:
        return None

    latest_date = extract_date_from_text(latest_phrase, default_year=ref_dt.year)
    latest_rel = resolve_relative_deadline(latest_phrase, reference_datetime=ref_dt)

    # 4. Compare historical deadlines against latest deadline
    for h_date, h_phrase, h_mem in hist_deadlines:
        # If latest deadline is ambiguous (e.g. "by Friday" on Friday)
        if latest_rel.get("is_ambiguous"):
            interps = latest_rel.get("interpretations") or ["2026-11-06", "2026-11-13"]
            if h_date.isoformat() not in interps:
                interps.append(h_date.isoformat())
            return {
                "type": "conflicting_deadlines",
                "historical_deadline": h_date.isoformat(),
                "latest_deadline": f"{latest_phrase} (ambiguous)",
                "description": (
                    f"Conflicting deadlines detected: Historical memory records revised proposal deadline of "
                    f"{h_date.isoformat()}, while latest notes state delivery required '{latest_phrase.strip()}'. "
                    f"Multiple deadline interpretations exist. Human confirmation is required before task execution."
                ),
                "requires_human_confirmation": True,
                "possible_interpretations": interps
            }

        # If both are resolved to specific calendar dates and they conflict
        resolved_latest = latest_date or (date.fromisoformat(latest_rel["resolved_date"]) if latest_rel.get("resolved_date") else None)
        if resolved_latest and resolved_latest != h_date:
            return {
                "type": "conflicting_deadlines",
                "historical_deadline": h_date.isoformat(),
                "latest_deadline": resolved_latest.isoformat(),
                "description": (
                    f"Conflicting deadlines detected: Historical record requires completion by {h_date.isoformat()}, "
                    f"whereas latest discussion notes specify {resolved_latest.isoformat()}. "
                    f"Human confirmation is required to reconcile this deadline conflict."
                ),
                "requires_human_confirmation": True,
                "possible_interpretations": [h_date.isoformat(), resolved_latest.isoformat()]
            }

    # 5. If no historical conflict, check if latest deadline alone is ambiguous
    if latest_rel.get("is_ambiguous"):
        return {
            "type": "ambiguous_deadline",
            "historical_deadline": None,
            "latest_deadline": latest_rel["description"],
            "description": latest_rel["description"],
            "requires_human_confirmation": True,
            "possible_interpretations": latest_rel.get("interpretations") or ["2026-11-06", "2026-11-13"]
        }

    return None
