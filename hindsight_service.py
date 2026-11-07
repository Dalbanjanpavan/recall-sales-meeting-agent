"""
Hindsight Service for Recall — Sales Meeting Intelligence Agent.
Integrates with Hindsight Cloud for long-term episodic memory, tagged prospect isolation,
and confirmed outcome learning.
Provides resilient failure handling and synchronization tracking.
"""

import os
import logging
import threading
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from dotenv import load_dotenv
from hindsight_client import Hindsight
from memory_processor import normalize_prospect_id, clean_and_deduplicate_memories

load_dotenv()

logger = logging.getLogger(__name__)

BANK_ID = "sales-meeting-agent-demo"
MAX_MEMORIES = 12


class HindsightService:
    """Manages memory retention, tagged retrieval, and prospect isolation with Hindsight."""

    def __init__(self, client: Optional[Hindsight] = None, offline_mode: bool = False):
        self._offline_mode = offline_mode
        self._custom_client = client
        self._thread_local = threading.local()
        self.is_connected = False
        self._bank_ready = bool(client)
        self.last_recall_status = "not_attempted"
        self.last_retain_status = "not_attempted"

        if offline_mode:
            return

        if client:
            self._custom_client = client
            self.is_connected = True
        else:
            api_key = os.getenv("HINDSIGHT_API_KEY")
            base_url = os.getenv("HINDSIGHT_BASE_URL")

            if not api_key or not base_url:
                logger.warning("Hindsight API credentials missing in .env. Service will operate in offline mode.")
                return

            # Client construction is intentionally local and non-blocking. Bank
            # provisioning is performed lazily on the first real operation so
            # importing Flask (and running tests) never performs network I/O.
            try:
                if self._get_client():
                    self.is_connected = True
            except Exception as e:
                logger.warning("Could not initialize Hindsight client: %s", e)
                self.is_connected = False

    def _ensure_bank(self) -> Optional[Hindsight]:
        """Return a usable client and lazily ensure the configured bank exists."""
        client = self._get_client()
        if not client:
            return None
        if self._bank_ready:
            return client
        try:
            client.create_bank(bank_id=BANK_ID, name="Sales Meeting Intelligence")
            self._bank_ready = True
            self.is_connected = True
        except Exception as e:
            logger.warning("Hindsight bank is unavailable: %s", e)
            self.is_connected = False
            return None
        return client

    def _get_client(self) -> Optional[Hindsight]:
        """Provides a thread-local Hindsight client to avoid cross-thread event loop issues in Flask."""
        if self._offline_mode:
            return None
        if self._custom_client is not None:
            return self._custom_client
        if hasattr(self._thread_local, "client") and self._thread_local.client is not None:
            return self._thread_local.client

        api_key = os.getenv("HINDSIGHT_API_KEY")
        base_url = os.getenv("HINDSIGHT_BASE_URL")
        if not api_key or not base_url:
            return None

        try:
            timeout = float(os.getenv("HINDSIGHT_TIMEOUT_SECONDS", "10"))
            max_attempts = int(os.getenv("HINDSIGHT_MAX_ATTEMPTS", "2"))
            cl = Hindsight(base_url=base_url, api_key=api_key, timeout=timeout, max_attempts=max_attempts)
            self._thread_local.client = cl
            return cl
        except Exception as e:
            logger.warning(f"Error creating thread-local Hindsight client: {e}")
            return None

    @property
    def client(self) -> Optional[Hindsight]:
        return self._get_client()

    @client.setter
    def client(self, val: Optional[Hindsight]):
        self._custom_client = val
        if val is not None:
            self.is_connected = True

    def save_memory(
        self,
        content: str,
        prospect_id: Optional[str] = None,
        prospect_name: Optional[str] = None,
        company: Optional[str] = None,
        document_id: Optional[str] = None,
        timestamp: Optional[str] = None
    ) -> Any:
        """
        Store a meeting interaction or update in Hindsight with prospect tagging and metadata.
        """
        if not content or not content.strip():
            raise ValueError("Cannot save empty meeting information.")

        client = self._ensure_bank()
        if not client:
            raise RuntimeError("Hindsight client is unavailable or offline.")

        tags = []
        metadata = {}

        if prospect_id:
            tags.append(f"prospect:{prospect_id}")
            metadata["prospect_id"] = prospect_id

        if prospect_name:
            metadata["prospect_name"] = prospect_name
        if company:
            metadata["company"] = company

        kwargs: Dict[str, Any] = {
            "bank_id": BANK_ID,
            "content": content.strip()
        }

        if tags:
            kwargs["tags"] = tags
        if metadata:
            kwargs["metadata"] = metadata
        if document_id:
            kwargs["document_id"] = document_id
        if timestamp:
            kwargs["timestamp"] = timestamp

        try:
            result = client.retain(**kwargs)
            self.last_retain_status = "succeeded"
            return result
        except Exception as e:
            logger.warning(f"Failed to retain with tags/metadata, falling back to simple retain: {e}")
            try:
                result = client.retain(bank_id=BANK_ID, content=content.strip())
                self.last_retain_status = "succeeded_with_fallback"
                return result
            except Exception:
                self.last_retain_status = "failed"
                raise

    def save_confirmed_outcome(
        self,
        prospect_id: str,
        prospect_name: str,
        company: str,
        task_description: str,
        outcome_note: str,
        task_id: Optional[str] = None,
        timestamp: Optional[str] = None
    ) -> Any:
        """
        Retain a human-confirmed task outcome as an authoritative factual customer event.
        Uses document_id keyed to task_id to ensure idempotency and prevent duplicate retention on retries.
        """
        if not self._ensure_bank():
            raise RuntimeError("Hindsight client is unavailable or offline.")

        ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        outcome_content = (
            f"CONFIRMED TASK OUTCOME ({ts}):\n"
            f"Prospect: {prospect_name}\n"
            f"Company: {company}\n"
            f"Action/Task Completed: {task_description}\n"
            f"Confirmed Outcome Note: {outcome_note}\n"
            f"Status: Completed and Verified by Sales Team."
        )

        doc_id = f"outcome-{task_id}" if task_id else f"outcome-{prospect_id}-{int(datetime.now(timezone.utc).timestamp())}"

        return self.save_memory(
            content=outcome_content,
            prospect_id=prospect_id,
            prospect_name=prospect_name,
            company=company,
            document_id=doc_id,
            timestamp=ts
        )

    def search_memories(
        self,
        query: str,
        prospect_name: str,
        company: Optional[str] = None,
        prospect_id: Optional[str] = None
    ) -> Tuple[List[str], int, int]:
        """
        Retrieve relevant memories for a prospect.
        Uses tagged recall for prospect isolation, supplemented by strict application-level
        filtering and deduplication.
        Returns:
            (unique_memory_texts, unique_count, raw_retrieved_count)
        """
        if not query or not query.strip():
            self.last_recall_status = "not_attempted"
            return [], 0, 0

        if not prospect_name or not prospect_name.strip():
            self.last_recall_status = "not_attempted"
            return [], 0, 0

        if not self.client:
            logger.warning("Hindsight client unavailable; returning empty memory set.")
            self.last_recall_status = "unavailable"
            return [], 0, 0

        company = company or ""
        p_id = prospect_id or normalize_prospect_id(prospect_name, company)

        raw_results = []

        # 1. First attempt: Tagged recall (strict isolation)
        try:
            tag = f"prospect:{p_id}"
            result = self.client.recall(
                bank_id=BANK_ID,
                query=query.strip(),
                tags=[tag],
                tags_match="all_strict",
            )
            if result and hasattr(result, "results") and result.results:
                raw_results.extend(result.results)
            self.last_recall_status = "succeeded"
        except Exception as e:
            logger.debug(f"Tagged recall attempt notice: {e}")
            self.last_recall_status = "failed"

        # Do not fall back to an untagged search. An empty tagged result is
        # safer than blending history from prospects with a shared name.

        raw_count = len(raw_results)

        # 3. Clean, isolate, and deduplicate memories
        cleaned = clean_and_deduplicate_memories(
            raw_memories=raw_results,
            prospect_id=p_id,
            prospect_name=prospect_name,
            company=company
        )

        unique_texts = [item["text"] for item in cleaned][:MAX_MEMORIES]
        return unique_texts, len(unique_texts), raw_count

    def close(self):
        """Close thread-local and custom Hindsight clients cleanly."""
        try:
            if hasattr(self._thread_local, "client") and self._thread_local.client:
                self._thread_local.client.close()
                self._thread_local.client = None
            if self._custom_client:
                self._custom_client.close()
        except Exception:
            pass
