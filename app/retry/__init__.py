"""
Stage 7: Retry Manager
Triggers targeted re-extraction only for problematic fields.
Does NOT re-extract the full schema — only fields that failed reliability checks.
"""
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Thresholds that trigger a retry
_MIN_GROUNDING_SCORE = 0.8
_MIN_CONFIDENCE_SCORE = 0.75

_RETRY_PROMPT = """Previous extraction failed verification. Re-extract ONLY the following fields from the document.

Problematic fields:
{field_list}

Reasons:
{reasons}

Document:
{document_text}

Return valid JSON with ONLY the requested fields. No extra text.
Example: {{"field_name": "extracted value"}}"""


class RetryManager:
    """
    Evaluates reliability signals and performs targeted field-level retries.
    """

    def __init__(self, groq_provider=None, max_retries: int = 2):
        if groq_provider is None:
            from app.providers.groq_provider import get_groq_provider
            groq_provider = get_groq_provider()
        self.groq = groq_provider
        self.max_retries = max_retries

    def should_retry(
        self,
        grounding_score: float,
        final_confidence: float,
        hallucination_report: Dict[str, Any],
        verifier_report: Dict[str, Any],
        schema_valid: bool,
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Determine whether a retry is needed and which fields to target.

        Returns:
            (needs_retry, problematic_fields, reasons)
        """
        problematic: List[str] = []
        reasons: List[str] = []

        # Missing required fields (schema validation failed)
        if not schema_valid:
            reasons.append("Schema validation failed — required fields missing or wrong type")

        # Hallucinated required fields
        hallucinated = hallucination_report.get("hallucinated_fields", [])
        if hallucinated:
            problematic.extend(hallucinated)
            reasons.append(f"Hallucinated fields: {hallucinated}")

        # Poor grounding
        if grounding_score < _MIN_GROUNDING_SCORE:
            reasons.append(f"Grounding score {grounding_score:.2f} below threshold {_MIN_GROUNDING_SCORE}")

        # LLM consistency failures
        issues = verifier_report.get("issues", [])
        if issues:
            failed_fields = [i["field"] for i in issues if "field" in i]
            problematic.extend(failed_fields)
            reasons.append(f"Consistency issues in fields: {failed_fields}")

        # Low final confidence
        if final_confidence < _MIN_CONFIDENCE_SCORE:
            reasons.append(f"Final confidence {final_confidence:.2f} below threshold {_MIN_CONFIDENCE_SCORE}")

        # Deduplicate
        problematic = list(dict.fromkeys(problematic))
        needs_retry = bool(reasons)
        return needs_retry, problematic, reasons

    def retry(
        self,
        document_text: str,
        current_data: Dict[str, Any],
        schema_dict: Dict[str, Any],
        problematic_fields: List[str],
        reasons: List[str],
        model: str = "llama-3.3-70b-versatile",
        attempt: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """
        Re-extract only the problematic fields.

        Returns:
            Merged data dict with retried field values, or None if retry failed.
        """
        if attempt > self.max_retries:
            logger.warning(f"Max retries ({self.max_retries}) reached, returning current data")
            return None

        # If no specific fields identified, retry all required fields
        if not problematic_fields:
            fields = schema_dict.get("fields", {})
            problematic_fields = [
                f for f, cfg in fields.items() if cfg.get("required", True)
            ]

        doc_snippet = document_text[:4000] if len(document_text) > 4000 else document_text

        prompt = _RETRY_PROMPT.format(
            field_list="\n".join(f"- {f}" for f in problematic_fields),
            reasons="\n".join(f"- {r}" for r in reasons),
            document_text=doc_snippet,
        )

        try:
            from app.extraction.structured_output import _extract_json_from_response
            raw = self.groq.generate(
                prompt=prompt,
                model=model,
                temperature=0.0,
                max_tokens=1024,
            )
            retried = _extract_json_from_response(raw)

            # Merge: retried values overwrite current data for problematic fields only
            merged = dict(current_data)
            for field in problematic_fields:
                if field in retried:
                    merged[field] = retried[field]

            logger.info(f"Retry attempt {attempt} updated fields: {list(retried.keys())}")
            return merged

        except Exception as e:
            logger.error(f"Retry attempt {attempt} failed: {e}")
            return None
