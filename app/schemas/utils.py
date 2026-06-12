"""Schema utilities for hashing, validation, and dynamic model generation."""
import hashlib
import json
from typing import Dict, Any, Type, Optional
from pydantic import BaseModel, create_model, field_validator
import logging

logger = logging.getLogger(__name__)


def hash_schema(schema_dict: Dict[str, Any]) -> str:
    """Generate SHA256 hash of schema for deduplication."""
    schema_json = json.dumps(schema_dict, sort_keys=True)
    return hashlib.sha256(schema_json.encode()).hexdigest()


def validate_schema_dict(schema_dict: Dict[str, Any]) -> bool:
    """Validate schema dictionary structure."""
    if not isinstance(schema_dict, dict):
        return False
    if "fields" not in schema_dict:
        return False
    if not isinstance(schema_dict["fields"], dict):
        return False
    return True


def create_dynamic_model(name: str, fields_dict: Dict[str, Any]) -> Type[BaseModel]:
    """
    Create a dynamic Pydantic model from schema fields.
    """
    field_definitions = {}

    # Updated to handle both standard JSON schema "array" and your custom "list" token safely
    type_mapping = {
        "string": str,
        "integer": int,
        "float": float,
        "boolean": bool,
        "array": List[Any],
        "list": List[Any], 
    }

    for field_name, field_config in fields_dict.items():
        field_type = field_config.get("type", "string")
        required = field_config.get("required", True)

        python_type = type_mapping.get(field_type, str)

        if not required:
            field_definitions[field_name] = (Optional[python_type], None)
        else:
            field_definitions[field_name] = (python_type, ...)

    return create_model(name, **field_definitions)


def validate_data_against_schema(data: Dict[str, Any], schema_dict: Dict[str, Any]) -> tuple:
    """
    Validate data against a schema.

    Args:
        data: Data to validate
        schema_dict: Schema definition

    Returns:
        (is_valid, error_message)
    """
    try:
        dynamic_model = create_dynamic_model("ValidationModel", schema_dict.get("fields", {}))
        dynamic_model(**data)
        return True, None
    except Exception as e:
        return False, str(e)
