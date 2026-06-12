from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from app.database.connection import get_db
from app.schemas.models import ExtractionJob
from app.extraction.structured_output import StructuredOutputEngine
from app.parser.document_parser import parse_document
import logging
import uuid
import json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extract", tags=["extraction"])
extraction_engine = StructuredOutputEngine()


class ExtractionRequest(BaseModel):
    """Extraction request — inline schema only."""
    document_content: str = Field(..., description="Text content to extract information from",
        example="Invoice #INV-2024-001\nCustomer: Acme Corp\nAmount: $5000")
    extraction_schema: Dict[str, Any] = Field(..., alias="schema",
        description="Field definitions. Each key is a field name, value is {type, required}.",
        example={"invoice_number": {"type": "string", "required": True}, "amount": {"type": "float", "required": True}})
    model: Optional[str] = Field("llama-3.3-70b-versatile", description="LLM model to use")
    method: Optional[str] = Field("llm", description="Extraction method: llm or regex")

    model_config = {"populate_by_name": True}


class ExtractionResponse(BaseModel):
    """Extraction response with full reliability pipeline signals."""
    extraction_id: str = Field(..., description="Unique extraction request ID")
    success: bool = Field(..., description="True only when all pipeline stages pass")
    data: Optional[Dict[str, Any]] = Field(None, description="Extracted structured data")
    error: Optional[str] = Field(None, description="Schema validation error if present")
    method: Optional[str] = Field(None, description="Extraction method used: llm or regex")
    schema_valid: Optional[bool] = Field(None, description="Whether Pydantic schema validation passed")
    grounding: Optional[Dict[str, str]] = Field(None, description="Per-field grounding: grounded | partial | ungrounded | skipped")
    grounding_score: Optional[float] = Field(None, description="Aggregate grounding score (0.0–1.0)")
    grounding_evidence: Optional[Dict[str, Any]] = Field(None, description="Per-field evidence snippets explaining the grounding decision")
    hallucination: Optional[Dict[str, Any]] = Field(None, description="Hallucination report derived from grounding")
    structural_issues: Optional[list] = Field(None, description="Deterministic structural validation issues")
    consistency: Optional[Dict[str, Any]] = Field(None, description="LLM consistency verifier report")
    confidence: Optional[float] = Field(None, description="Final reliability score (0.0–1.0)")
    field_confidence: Optional[Dict[str, float]] = Field(None, description="Per-field confidence scores")


def _build_response(extraction_id: str, result: Dict[str, Any]) -> ExtractionResponse:
    return ExtractionResponse(
        extraction_id=extraction_id,
        success=result["success"],
        data=result.get("data"),
        error=result.get("error"),
        method=result.get("method"),
        schema_valid=result.get("schema_valid"),
        grounding=result.get("grounding"),
        grounding_score=result.get("grounding_score"),
        grounding_evidence=result.get("grounding_evidence"),
        hallucination=result.get("hallucination"),
        structural_issues=result.get("structural_issues"),
        consistency=result.get("consistency"),
        confidence=result.get("confidence"),
        field_confidence=result.get("field_confidence"),
    )


@router.post(
    "/",
    response_model=ExtractionResponse,
    summary="Extract data from document text",
    description="""
Extract structured data from raw document text using a dynamic inline schema.

**Schema format:**
```json
{
  "field_name": {"type": "string|integer|float|boolean|array|object", "required": true|false}
}
```

**Example:**
```json
{
  "document_content": "Invoice #123 from Acme Corp. Amount: $5000",
  "schema": {
    "invoice_number": {"type": "string", "required": true},
    "amount": {"type": "float", "required": true}
  },
  "model": "llama-3.3-70b-versatile",
  "method": "llm"
}
```
""",
)
async def extract(request: ExtractionRequest):
    try:
        extraction_id = str(uuid.uuid4())
        schema_dict = {"fields": request.extraction_schema}

        if request.method == "regex":
            result = extraction_engine.extract_regex(request.document_content, schema_dict)
        else:
            result = extraction_engine.extract(request.document_content, schema_dict, request.model)

        return _build_response(extraction_id, result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Extraction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/upload",
    response_model=ExtractionResponse,
    summary="Extract data from uploaded file",
    description="""
Extract structured data from an uploaded document file using a dynamic inline schema.

**Supported file formats:** PDF, HTML, TXT

**`schema` field (JSON string):**
```json
{"invoice_number": {"type": "string", "required": true}, "amount": {"type": "float", "required": true}}
```

Supports full schema objects with a top-level `"fields"` key, or flat field dicts directly.
""",
    responses={
        200: {"description": "Extraction result with full reliability signals"},
        400: {"description": "Missing or invalid schema"},
        500: {"description": "Extraction or parsing error"},
    },
)
async def extract_from_file(
    file: UploadFile = File(..., description="Document file to extract from. Supported: PDF, HTML, TXT"),
    extraction_schema: str = Form(
        ..., alias="schema",
        description='Schema as JSON string. Example: `{"invoice_number": {"type": "string", "required": true}}`',
    ),
    model: Optional[str] = Form("llama-3.3-70b-versatile", description="LLM model to use"),
    method: Optional[str] = Form("llm", description="Extraction method: `llm` (default) or `regex`"),
):
    try:
        extraction_id = str(uuid.uuid4())

        file_bytes = await file.read()
        file_type = file.filename.split(".")[-1].lower()
        document_text = parse_document(file_bytes, file_type)

        parsed = json.loads(extraction_schema)
        if "fields" in parsed:
            schema_dict = {"fields": parsed["fields"]}
        else:
            schema_dict = {"fields": parsed}

        if not schema_dict["fields"]:
            raise HTTPException(status_code=400, detail="Schema must define at least one field")

        if method == "regex":
            result = extraction_engine.extract_regex(document_text, schema_dict)
        else:
            result = extraction_engine.extract(document_text, schema_dict, model)

        return _build_response(extraction_id, result)

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid schema JSON: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File extraction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Batch extraction models
# ---------------------------------------------------------------------------

class BatchExtractionRequest(BaseModel):
    """Submit multiple documents for async extraction."""
    documents: List[str] = Field(..., description="List of document text strings to extract from",
        min_length=1, example=["Invoice #123 from Acme Corp. Amount: $5000"])
    extraction_schema: Dict[str, Any] = Field(..., alias="schema",
        description="Field definitions applied to every document in the batch",
        example={"invoice_number": {"type": "string", "required": True}, "amount": {"type": "float", "required": True}})
    model: Optional[str] = Field("llama-3.3-70b-versatile", description="LLM model to use")
    method: Optional[str] = Field("llm", description="Extraction method: llm or regex")

    model_config = {"populate_by_name": True}


class BatchJobResponse(BaseModel):
    """Immediate response after submitting a batch job."""
    job_id: str = Field(..., description="UUID to poll for status")
    status: str = Field(..., description="Initial job status: pending")
    total: int = Field(..., description="Number of documents queued")
    message: str = Field(..., description="Human-readable status message")


class JobStatusResponse(BaseModel):
    """Live job status returned by GET /extract/jobs/{job_id}."""
    job_id: str
    status: str = Field(..., description="pending | started | completed | partial | failed")
    total: int
    processed: int
    succeeded: int
    failed: int
    progress_pct: float = Field(..., description="Percentage of documents processed")
    model: Optional[str]
    method: Optional[str]
    results: Optional[List[Dict[str, Any]]] = Field(None, description="Per-document results (available when completed)")
    error: Optional[str]
    created_at: str
    completed_at: Optional[str]


@router.post(
    "/batch",
    response_model=BatchJobResponse,
    summary="Submit batch extraction job (async)",
    description="""
Submit multiple documents for extraction in a background Celery job.

Returns a `job_id` immediately. Poll `GET /extract/jobs/{job_id}` for progress.

- Each document is extracted independently using the same schema.
- Results are stored in the database as they complete.
- Final status: `completed` (all ok) | `partial` (some failed) | `failed` (all failed)
""",
)
async def extract_batch(request: BatchExtractionRequest, db: Session = Depends(get_db)):
    from app.workers.tasks import extract_batch as celery_task

    job_id = str(uuid.uuid4())
    schema_dict = {"fields": request.extraction_schema}

    # Persist job record immediately so GET /jobs/{id} works right away
    job = ExtractionJob(
        id=job_id,
        status="pending",
        total=len(request.documents),
        processed=0,
        succeeded=0,
        failed=0,
        schema_json=schema_dict,
        model=request.model,
        method=request.method,
    )
    db.add(job)
    db.commit()

    # Dispatch to Celery — non-blocking
    celery_task.delay(
        job_id=job_id,
        documents=request.documents,
        schema_json=schema_dict,
        model=request.model,
        method=request.method,
    )

    return BatchJobResponse(
        job_id=job_id,
        status="pending",
        total=len(request.documents),
        message=f"Job queued. {len(request.documents)} documents will be processed asynchronously.",
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Get batch job status",
    description="Poll this endpoint to track the progress of an async batch extraction job.",
)
async def get_job_status(job_id: str, db: Session = Depends(get_db)):
    job = db.get(ExtractionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    progress_pct = round(job.processed / job.total * 100, 1) if job.total else 0.0

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        total=job.total,
        processed=job.processed,
        succeeded=job.succeeded,
        failed=job.failed,
        progress_pct=progress_pct,
        model=job.model,
        method=job.method,
        results=job.results if job.status in ("completed", "partial", "failed") else None,
        error=job.error,
        created_at=job.created_at.isoformat(),
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )
