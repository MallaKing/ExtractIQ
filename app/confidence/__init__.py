"""
Stage 6: Confidence Scorer
Combines signals from all previous stages into a single reliability score.

Formula:
  final_score = 0.35 * grounding_score
              + 0.25 * completeness_score
              + 0.20 * schema_score
              + 0.20 * verifier_score

Grounding multipliers per field:
  grounded   -> 1.0
  partial    -> 0.7
  ungrounded -> 0.2
  skipped    -> 0.0  (excluded from per-field average)
"""
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)

_GROUNDING_MULTIPLIERS = {
    "grounded": 1.0,
    "partial": 0.7,
    "ungrounded": 0.2,
    "skipped": None,   # excluded
}


class ConfidenceScorer:

    def score(
        self,
        data: Dict[str, Any],
        schema_dict: Dict[str, Any],
        grounding: Dict[str, str],
        schema_valid: bool,
        structural_issues: list,
        verifier_score: float,
    ) -> Tuple[float, Dict[str, float]]:
        """
        Args:
            data: Extracted data
            schema_dict: Schema with 'fields' key
            grounding: Per-field grounding status
            schema_valid: Whether Pydantic schema validation passed
            structural_issues: List of structural validator issues
            verifier_score: Score from LLM consistency verifier (0.0–1.0)

        Returns:
            (final_score, per_field_scores)
        """
        fields = schema_dict.get("fields", {})

        # --- Grounding score (per-field weighted) ---
        grounding_values = []
        per_field: Dict[str, float] = {}

        for field_name in fields:
            status = grounding.get(field_name, "skipped")
            multiplier = _GROUNDING_MULTIPLIERS.get(status)
            if multiplier is None:
                per_field[field_name] = 0.0
                continue
            per_field[field_name] = round(multiplier, 3)
            grounding_values.append(multiplier)

        grounding_score = round(
            sum(grounding_values) / len(grounding_values), 3
        ) if grounding_values else 1.0

        # --- Completeness score ---
        # Fraction of required fields that are present and non-null
        required_fields = [
            f for f, cfg in fields.items() if cfg.get("required", True)
        ]
        if required_fields:
            present = sum(
                1 for f in required_fields
                if data.get(f) is not None and data.get(f) != ""
            )
            completeness_score = round(present / len(required_fields), 3)
        else:
            completeness_score = 1.0

        # --- Schema score ---
        # 1.0 if valid, penalised per structural issue
        schema_score = 1.0 if schema_valid else 0.5
        if structural_issues:
            schema_score = max(0.0, round(schema_score - len(structural_issues) * 0.1, 3))

        # --- Final weighted score ---
        final_score = round(
            0.35 * grounding_score
            + 0.25 * completeness_score
            + 0.20 * schema_score
            + 0.20 * verifier_score,
            3,
        )

        logger.debug(
            f"Confidence: final={final_score} grounding={grounding_score} "
            f"completeness={completeness_score} schema={schema_score} verifier={verifier_score}"
        )

        return final_score, per_field
