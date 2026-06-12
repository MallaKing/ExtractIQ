"""
Stage 5: LLM Consistency Verifier
The only stage that uses an LLM.
Focuses exclusively on semantic reasoning:
  - Arithmetic correctness
  - Counting accuracy
  - Contradictions with document
  - Unsupported inferences
Does NOT re-check types, missing fields, grounding, or structural issues.
"""
import json
import logging
from typing import Dict, Any, Tuple, List

logger = logging.getLogger(__name__)

_VERIFIER_PROMPT = """You are a data verification assistant. Your job is to check whether extracted data is logically consistent with the source document.

You must ONLY check for:
1. Arithmetic errors (sums, totals, percentages that do not add up)
2. Counting errors (counts that do not match items listed in the document)
3. Contradictions (extracted value directly contradicts the document)
4. Unsupported inferences (value was inferred/assumed rather than stated in the document)

You must NOT check or report:
- Missing fields
- Wrong types
- Schema violations
- Grounding or text presence (already handled separately)

Source Document:
{document_text}

Extracted Data:
{extracted_data}

Grounding Report (for context only — do not re-report grounding issues):
{grounding_report}

Respond with valid JSON only, no extra text:
{{
  "consistent": true,
  "issues": []
}}

Or if issues found:
{{
  "consistent": false,
  "issues": [
    {{
      "field": "field_name",
      "reason": "Clear explanation of the inconsistency"
    }}
  ]
}}"""


class LLMConsistencyVerifier:
    """
    Uses an LLM to verify semantic consistency of extracted data.
    """

    def __init__(self, groq_provider=None):
        if groq_provider is None:
            from app.providers.groq_provider import get_groq_provider
            groq_provider = get_groq_provider()
        self.groq = groq_provider

    def verify(
        self,
        document_text: str,
        extracted_data: Dict[str, Any],
        grounding: Dict[str, str],
        model: str = "llama-3.3-70b-versatile",
    ) -> Tuple[Dict[str, Any], float]:
        """
        Args:
            document_text: Source document
            extracted_data: Extraction result
            grounding: Grounding report from GroundingChecker
            model: LLM model to use

        Returns:
            (verifier_report, verifier_score)
            verifier_score: 1.0 if consistent, reduced by 0.15 per issue (min 0.0)
        """
        # Truncate document to avoid token limits
        doc_snippet = document_text[:4000] if len(document_text) > 4000 else document_text

        prompt = _VERIFIER_PROMPT.format(
            document_text=doc_snippet,
            extracted_data=json.dumps(extracted_data, indent=2),
            grounding_report=json.dumps(grounding, indent=2),
        )

        try:
            result = self.groq.generate_json(
                prompt=prompt,
                model=model,
                temperature=0.0,
                max_tokens=1024,
            )

            consistent = result.get("consistent", True)
            issues: List[Dict[str, str]] = result.get("issues", [])

            # Ensure issues is a list of dicts with field + reason
            issues = [
                i for i in issues
                if isinstance(i, dict) and "field" in i and "reason" in i
            ]

            verifier_score = max(0.0, round(1.0 - len(issues) * 0.15, 3))

            report = {
                "consistent": consistent,
                "issues": issues,
            }

            if not consistent:
                logger.warning(f"Consistency issues: {issues}")

            return report, verifier_score

        except Exception as e:
            logger.error(f"LLM consistency verifier failed: {e}")
            # Fail open — don't block extraction on verifier error
            return {"consistent": True, "issues": [], "error": str(e)}, 1.0
