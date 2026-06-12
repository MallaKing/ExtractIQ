"""Prompt compiler that converts schemas to extraction prompts."""
import json
from typing import Dict, Any


def compile_extraction_prompt(schema: Dict[str, Any], document_text: str) -> str:
    """Compile extraction prompt optimized for robust JSON output."""
    fields = schema.get("fields", {})
    
    field_descriptions = []
    for field_name, field_config in fields.items():
        field_type = field_config.get("type", "string")
        required = field_config.get("required", True)
        description = field_config.get("description", f"Extract {field_name}")
        req_str = "required" if required else "optional"
        field_descriptions.append(f"  - {field_name} ({field_type}, {req_str}): {description}")
    
    fields_section = "\n".join(field_descriptions)
    
    prompt = f"""Extract the following information from the document and return ONLY valid JSON:

{fields_section}

Document:
{document_text}

Return the extracted data as valid JSON with exact field names. No additional text. If a field is optional and not found, omit it or set to null.
{{
  // JSON output only, no markdown
}}"""
    
    return prompt


def compile_tool_prompt(schema: Dict[str, Any], document_text: str) -> tuple[str, Dict]:
    """Compile extraction prompt with tool/function format."""
    fields = schema.get("fields", {})
    
    # Build tool definition
    properties = {}
    required_fields = []
    
    for field_name, field_config in fields.items():
        field_type = field_config.get("type", "string")
        type_mapping = {
            "string": "string",
            "integer": "integer",
            "float": "number",
            "boolean": "boolean",
            "array": "array",
            "object": "object",
        }
        
        properties[field_name] = {
            "type": type_mapping.get(field_type, "string"),
            "description": field_config.get("description", f"Extract {field_name}")
        }
        
        if field_config.get("required", True):
            required_fields.append(field_name)
    
    tool_definition = {
        "name": "extract_information",
        "description": "Extract structured information from document",
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required_fields
        }
    }
    
    prompt = f"""Extract information from this document and call the extract_information function with the results:

{document_text}"""
    
    return prompt, tool_definition
