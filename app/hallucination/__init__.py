"""
Stage 3: Hallucination Detector
Derived entirely from grounding results — no LLM, no separate logic.
required + ungrounded = hallucination
optional + ungrounded = uncertain
"""
import logging
from typing import Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class HallucinationDetector:
    """
    Derives hallucination report from grounding results.
    Does not perform any independent checks.
    """

    def detect(
        self,
        grounding: Dict[str, str],
        schema_dict: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Args:
            grounding: Per-field grounding status from GroundingChecker
            schema_dict: Schema with 'fields' key

        Returns:
            (hallucination_report, is_clean)
        """
        fields = schema_dict.get("fields", {})
        hallucinated: List[str] = []
        uncertain: List[str] = []
        checked = 0

        for field_name, status in grounding.items():
            if status == "skipped":
                continue

            checked += 1
            required = fields.get(field_name, {}).get("required", True)

            if status == "ungrounded":
                if required:
                    hallucinated.append(field_name)
                else:
                    uncertain.append(field_name)

        hallucination_rate = round(len(hallucinated) / checked, 3) if checked else 0.0
        is_clean = len(hallucinated) == 0

        if not is_clean:
            logger.warning(f"Hallucinations detected: {hallucinated}")

        return {
            "hallucinated_fields": hallucinated,
            "uncertain_fields": uncertain,
            "hallucination_rate": hallucination_rate,
            "is_clean": is_clean,
        }, is_clean
