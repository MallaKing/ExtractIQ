"""
Schema management — fully user-scoped.
Every schema belongs to a user. Users can only see/edit/delete their own schemas.
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional
from app.database.connection import get_db
from app.schemas.models import Schema as SchemaModel
from app.schemas.utils import hash_schema, validate_schema_dict
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schemas", tags=["schemas"])


class SchemaCreate(BaseModel):
    name: str = Field(..., example="InvoiceSchema")
    fields: dict = Field(..., example={"invoice_number": {"type": "string", "required": True}})
    description: Optional[str] = None
    version: Optional[str] = "v1"
    user_id: int = Field(..., description="Owner user ID")


class SchemaResponse(BaseModel):
    id: int
    name: str
    version: str
    fields: dict = Field(alias="schema_json")
    schema_hash: str
    description: Optional[str]
    user_id: Optional[int]

    model_config = {"from_attributes": True, "populate_by_name": True}


@router.post("/", response_model=SchemaResponse, summary="Create schema for a user")
async def create_schema(schema: SchemaCreate, db: Session = Depends(get_db)):
    schema_dict = {"fields": schema.fields}
    if not validate_schema_dict(schema_dict):
        raise HTTPException(status_code=400, detail="Invalid schema — must have a 'fields' dict")

    schema_hash = hash_schema(schema_dict)

    # Deduplicate per user (same user can't create identical schema twice)
    existing = db.query(SchemaModel).filter(
        SchemaModel.schema_hash == schema_hash,
        SchemaModel.user_id == schema.user_id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Identical schema already exists (ID {existing.id})")

    try:
        db_schema = SchemaModel(
            name=schema.name,
            version=schema.version,
            schema_json=schema_dict,
            schema_hash=schema_hash,
            description=schema.description,
            user_id=schema.user_id,
        )
        db.add(db_schema)
        db.commit()
        db.refresh(db_schema)
        logger.info(f"Created schema {db_schema.id} for user {schema.user_id}")
        return db_schema
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/{user_id}", response_model=List[SchemaResponse], summary="List schemas for a user")
async def list_user_schemas(user_id: int, db: Session = Depends(get_db)):
    return db.query(SchemaModel).filter(SchemaModel.user_id == user_id).order_by(SchemaModel.id.desc()).all()


@router.get("/{schema_id}", response_model=SchemaResponse, summary="Get schema by ID")
async def get_schema(schema_id: int, user_id: int, db: Session = Depends(get_db)):
    s = db.query(SchemaModel).filter(SchemaModel.id == schema_id, SchemaModel.user_id == user_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Schema not found")
    return s


@router.delete("/{schema_id}", summary="Delete a schema")
async def delete_schema(schema_id: int, user_id: int, db: Session = Depends(get_db)):
    s = db.query(SchemaModel).filter(SchemaModel.id == schema_id, SchemaModel.user_id == user_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Schema not found or not yours")
    try:
        db.delete(s)
        db.commit()
        return {"deleted": schema_id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
