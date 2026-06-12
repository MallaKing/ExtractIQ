"""
Stage 4: Structural Validator
Deterministic — no LLM.
Catches data-quality issues that pass schema type checks but are semantically invalid.
"""
import re
import logging
from typing import Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

# Simple email regex
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ISO date patterns: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, MM/DD/YYYY
_DATE_RES = [
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),          # YYYY-MM-DD
    re.compile(r"^\d{2}[/-]\d{2}[/-]\d{4}$"),     # DD/MM/YYYY or MM/DD/YYYY
]


def _looks_like_date_field(name: str) -> bool:
    keywords = ("date", "deadline", "due", "expiry", "start", "end", "dob", "created", "updated")
    return any(k in name.lower() for k in keywords)


def _looks_like_email_field(name: str) -> bool:
    return "email" in name.lower() or "mail" in name.lower()


def _looks_like_amount_field(name: str) -> bool:
    keywords = ("amount", "price", "cost", "fee", "salary", "revenue", "arr", "mrr", "total", "subtotal")
    return any(k in name.lower() for k in keywords)


class StructuralValidator:
    """
    Deterministic data-quality checks on extracted values.
    Operates independently of the LLM.
    """

    def validate(
        self,
        data: Dict[str, Any],
        schema_dict: Dict[str, Any],
    ) -> Tuple[bool, List[Dict[str, str]]]:
        """
        Returns:
            (passed, issues)
            issues: list of {field, check, detail}
        """
        fields = schema_dict.get("fields", {})
        issues: List[Dict[str, str]] = []

        for field_name, field_config in fields.items():
            value = data.get(field_name)
            field_type = field_config.get("type", "string")

            # Skip null/missing optional fields
            if value is None:
                continue

            # --- Empty value checks ---
            if isinstance(value, str) and not value.strip():
                issues.append({
                    "field": field_name,
                    "check": "empty_string",
                    "detail": "Field is an empty string",
                })
                continue

            if isinstance(value, list) and len(value) == 0:
                issues.append({
                    "field": field_name,
                    "check": "empty_array",
                    "detail": "Field is an empty array",
                })
                continue

            if isinstance(value, dict) and len(value) == 0:
                issues.append({
                    "field": field_name,
                    "check": "empty_object",
                    "detail": "Field is an empty object",
                })
                continue

            # --- Type-specific checks ---
            if field_type == "string" and isinstance(value, str):
                if _looks_like_email_field(field_name):
                    if not _EMAIL_RE.match(value.strip()):
                        issues.append({
                            "field": field_name,
                            "check": "invalid_email",
                            "detail": f"Value '{value}' does not look like a valid email",
                        })

                if _looks_like_date_field(field_name):
                    if not any(p.match(value.strip()) for p in _DATE_RES):
                        issues.append({
                            "field": field_name,
                            "check": "invalid_date_format",
                            "detail": f"Value '{value}' does not match expected date format (YYYY-MM-DD)",
                        })

            if field_type in ("integer", "float") and isinstance(value, (int, float)):
                if _looks_like_amount_field(field_name) and value < 0:
                    issues.append({
                        "field": field_name,
                        "check": "negative_amount",
                        "detail": f"Amount field '{field_name}' has negative value {value}",
                    })

            # --- Custom constraints from schema ---
            min_val = field_config.get("minimum")
            max_val = field_config.get("maximum")
            if min_val is not None and isinstance(value, (int, float)) and value < min_val:
                issues.append({
                    "field": field_name,
                    "check": "below_minimum",
                    "detail": f"Value {value} is below minimum {min_val}",
                })
            if max_val is not None and isinstance(value, (int, float)) and value > max_val:
                issues.append({
                    "field": field_name,
                    "check": "above_maximum",
                    "detail": f"Value {value} is above maximum {max_val}",
                })

            enum_vals = field_config.get("enum")
            if enum_vals and value not in enum_vals:
                issues.append({
                    "field": field_name,
                    "check": "invalid_enum",
                    "detail": f"Value '{value}' not in allowed values: {enum_vals}",
                })

        passed = len(issues) == 0
        if not passed:
            logger.warning(f"Structural validation issues: {issues}")
        return passed, issues
