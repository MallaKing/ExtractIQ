"""
Structured Output Engine
Orchestrates the full Extraction Reliability Pipeline v2:

  LLM/Regex Extraction
    → Schema Validation       (Pydantic)
    → Grounding Checker       (deterministic)
    → Hallucination Detector  (derived from grounding)
    → Structural Validator    (deterministic)
    → LLM Consistency Verifier
    → Confidence Scorer
    → Retry Manager
    → Final Output
"""
import json
import re
import logging
from typing import Dict, Any, Optional

from app.schemas.utils import validate_data_against_schema
from app.grounding import GroundingChecker, generate_field_aliases
from app.hallucination import HallucinationDetector
from app.validation import StructuralValidator
from app.evaluation import LLMConsistencyVerifier
from app.confidence import ConfidenceScorer
from app.retry import RetryManager

logger = logging.getLogger(__name__)


def _extract_json_from_response(content: str) -> Dict[str, Any]:
    """Extract JSON object from LLM response using brace-balanced parser."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    brace_count = 0
    start_idx = -1
    for i, char in enumerate(content):
        if char == "{":
            if brace_count == 0:
                start_idx = i
            brace_count += 1
        elif char == "}":
            brace_count -= 1
            if brace_count == 0 and start_idx != -1:
                try:
                    return json.loads(content[start_idx : i + 1])
                except json.JSONDecodeError:
                    pass

    raise json.JSONDecodeError("No valid JSON found in response", content, 0)


class StructuredOutputEngine:
    """
    Orchestrates the full reliability pipeline for document extraction.
    Accepts an optional groq_api_key for BYOK — uses platform key if not provided.
    """

    def __init__(self, groq_api_key: str = None):
        from app.providers.groq_provider import get_groq_provider, GroqProvider
        if groq_api_key:
            self.groq = GroqProvider(api_key=groq_api_key)
        else:
            self.groq = get_groq_provider()
        self.grounding_checker = GroundingChecker()
        self.hallucination_detector = HallucinationDetector()
        self.structural_validator = StructuralValidator()
        self.consistency_verifier = LLMConsistencyVerifier(self.groq)
        self.confidence_scorer = ConfidenceScorer()
        self.retry_manager = RetryManager(self.groq)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        document_text: str,
        schema: Dict[str, Any],
        model: str = "llama-3.3-70b-versatile",
    ) -> Dict[str, Any]:
        """LLM-based extraction with full reliability pipeline."""
        from app.prompt_compiler.compiler import compile_extraction_prompt

        prompt = compile_extraction_prompt(schema, document_text)

        try:
            raw = self.groq.generate(
                prompt=prompt,
                model=model,
                temperature=0.1,
                max_tokens=2048,
            )
            extracted_data = _extract_json_from_response(raw)
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")
            return self._error_result(str(e), "llm")

        return self._run_pipeline(
            document_text=document_text,
            extracted_data=extracted_data,
            schema=schema,
            model=model,
            method="llm",
        )

    def extract_regex(
        self,
        document_text: str,
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Regex-based extraction with full reliability pipeline (no LLM consistency check)."""
        extracted_data: Dict[str, Any] = {}
        fields = schema.get("fields", {})

        for field_name, field_config in fields.items():
            pattern = self._get_regex_pattern(field_name, field_config)
            match = re.search(pattern, document_text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                extracted_data[field_name] = self._cast_value(
                    value, field_config.get("type", "string")
                )

        return self._run_pipeline(
            document_text=document_text,
            extracted_data=extracted_data,
            schema=schema,
            model=None,   # no LLM verifier for regex path
            method="regex",
        )

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def _run_pipeline(
        self,
        document_text: str,
        extracted_data: Dict[str, Any],
        schema: Dict[str, Any],
        model: Optional[str],
        method: str,
        _retry_attempt: int = 0,
    ) -> Dict[str, Any]:
        """
        Runs all reliability stages on extracted data.
        Called recursively on retry (max once).
        """

        # Stage 1: Schema Validation
        schema_valid, schema_error = validate_data_against_schema(extracted_data, schema)

        # Stage 2: Grounding
        # Generate LLM aliases once for all field names
        field_names = list(schema.get("fields", {}).keys())
        field_aliases = generate_field_aliases(field_names, self.groq, model or "llama-3.3-70b-versatile")
        grounding, grounding_score = self.grounding_checker.check(
            extracted_data, document_text, schema, field_aliases=field_aliases
        )
        grounding_evidence = dict(self.grounding_checker.evidence)

        # Stage 3: Hallucination Detection (derived from grounding)
        hallucination_report, is_clean = self.hallucination_detector.detect(
            grounding, schema
        )

        # Stage 4: Structural Validation
        struct_passed, struct_issues = self.structural_validator.validate(
            extracted_data, schema
        )

        # Stage 5: LLM Consistency Verifier (LLM path only)
        if model and method == "llm":
            verifier_report, verifier_score = self.consistency_verifier.verify(
                document_text, extracted_data, grounding, model
            )
        else:
            verifier_report = {"consistent": True, "issues": []}
            verifier_score = 1.0

        # Stage 6: Confidence Scoring
        final_confidence, field_confidence = self.confidence_scorer.score(
            data=extracted_data,
            schema_dict=schema,
            grounding=grounding,
            schema_valid=schema_valid,
            structural_issues=struct_issues,
            verifier_score=verifier_score,
        )

        # Stage 7: Retry Manager
        if _retry_attempt < self.retry_manager.max_retries:
            needs_retry, problematic_fields, retry_reasons = self.retry_manager.should_retry(
                grounding_score=grounding_score,
                final_confidence=final_confidence,
                hallucination_report=hallucination_report,
                verifier_report=verifier_report,
                schema_valid=schema_valid,
            )

            if needs_retry and model:
                logger.info(
                    f"Retry attempt {_retry_attempt + 1} for fields: {problematic_fields}"
                )
                retried_data = self.retry_manager.retry(
                    document_text=document_text,
                    current_data=extracted_data,
                    schema_dict=schema,
                    problematic_fields=problematic_fields,
                    reasons=retry_reasons,
                    model=model,
                    attempt=_retry_attempt + 1,
                )
                if retried_data:
                    return self._run_pipeline(
                        document_text=document_text,
                        extracted_data=retried_data,
                        schema=schema,
                        model=model,
                        method=method,
                        _retry_attempt=_retry_attempt + 1,
                    )

        overall_success = (
            schema_valid
            and is_clean
            and struct_passed
            and verifier_report.get("consistent", True)
        )

        return {
            "success": overall_success,
            "data": extracted_data,
            "error": schema_error if not schema_valid else None,
            "method": method,
            # Stage signals
            "schema_valid": schema_valid,
            "grounding": grounding,
            "grounding_score": grounding_score,
            "grounding_evidence": grounding_evidence,
            "hallucination": hallucination_report,
            "structural_issues": struct_issues,
            "consistency": verifier_report,
            # Confidence
            "confidence": final_confidence,
            "field_confidence": field_confidence,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _error_result(self, error: str, method: str) -> Dict[str, Any]:
        return {
            "success": False,
            "data": None,
            "error": error,
            "method": method,
            "schema_valid": False,
            "grounding": {},
            "grounding_score": 0.0,
            "grounding_evidence": {},
            "hallucination": {
                "hallucinated_fields": [],
                "uncertain_fields": [],
                "hallucination_rate": 0.0,
                "is_clean": True,
            },
            "structural_issues": [],
            "consistency": {"consistent": True, "issues": []},
            "confidence": 0.0,
            "field_confidence": {},
        }

    def _get_regex_pattern(self, field_name: str, field_config: Dict) -> str:
        patterns = {
            "string": rf"{field_name}[:\s]+([^\n]+)",
            "integer": rf"{field_name}[:\s]+(\d+)",
            "float": rf"{field_name}[:\s]+([\d.]+)",
            "boolean": rf"{field_name}[:\s]+(true|false|yes|no)",
        }
        return patterns.get(field_config.get("type", "string"), patterns["string"])

    def _cast_value(self, value: str, field_type: str) -> Any:
        try:
            if field_type == "integer":
                return int(value)
            elif field_type == "float":
                return float(value)
            elif field_type == "boolean":
                return value.lower() in ["true", "yes", "1"]
        except (ValueError, TypeError):
            pass
        return value
