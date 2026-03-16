"""API routes for zone template management."""
import logging
import json
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.models import ZoneTemplate, User
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["Templates"])


class TemplateRecord(BaseModel):
    """A single record entry within a template."""
    name: str = Field(..., description="Record name, use @ for zone apex or subdomain")
    type: str = Field(..., description="Record type (A, AAAA, MX, CNAME, TXT, NS, etc.)")
    content: str = Field(..., description="Record content (IP, hostname, etc.)")
    ttl: int = Field(default=3600, description="TTL in seconds")
    prio: Optional[int] = Field(default=None, description="Priority (for MX, SRV)")


class TemplateCreate(BaseModel):
    """Schema for creating a template."""
    name: str = Field(..., min_length=1, max_length=255, description="Template name")
    description: Optional[str] = None
    nameservers: list[str] = Field(default_factory=list, description="Default nameservers")
    kind: str = Field(default="Native")
    soa_edit_api: str = Field(default="DEFAULT")
    default_ttl: int = Field(default=3600)
    records: list[TemplateRecord] = Field(default_factory=list, description="Default records")
    is_default: bool = Field(default=False, description="Is this the default template?")


class TemplateUpdate(BaseModel):
    """Schema for updating a template."""
    name: Optional[str] = None
    description: Optional[str] = None
    nameservers: Optional[list[str]] = None
    kind: Optional[str] = None
    soa_edit_api: Optional[str] = None
    default_ttl: Optional[int] = None
    records: Optional[list[TemplateRecord]] = None
    is_default: Optional[bool] = None


@router.get("")
async def list_templates(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all zone templates."""
    result = await db.execute(select(ZoneTemplate).order_by(ZoneTemplate.name))
    templates = result.scalars().all()
    
    out = []
    for t in templates:
        records_data = t.records if isinstance(t.records, (list, dict)) else json.loads(t.records) if t.records else {}
        out.append({
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "nameservers": records_data.get("nameservers", []),
            "kind": records_data.get("kind", "Native"),
            "soa_edit_api": records_data.get("soa_edit_api", "DEFAULT"),
            "default_ttl": records_data.get("default_ttl", 3600),
            "records": records_data.get("records", []),
            "is_default": records_data.get("is_default", False),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        })
    
    return {"templates": out}


@router.post("")
async def create_template(
    data: TemplateCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new zone template."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Nur Admins können Vorlagen erstellen")
    
    # Check for duplicate name
    existing = await db.execute(select(ZoneTemplate).where(ZoneTemplate.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Vorlage '{data.name}' existiert bereits")
    
    # If this is set as default, unset other defaults
    if data.is_default:
        all_templates = await db.execute(select(ZoneTemplate))
        for t in all_templates.scalars().all():
            rec = t.records if isinstance(t.records, (list, dict)) else json.loads(t.records) if t.records else {}
            if rec.get("is_default"):
                rec["is_default"] = False
                t.records = rec
    
    records_json = {
        "nameservers": data.nameservers,
        "kind": data.kind,
        "soa_edit_api": data.soa_edit_api,
        "default_ttl": data.default_ttl,
        "records": [r.model_dump() for r in data.records],
        "is_default": data.is_default,
    }
    
    template = ZoneTemplate(
        name=data.name,
        description=data.description,
        records=records_json,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    
    return {"message": f"Vorlage '{data.name}' erstellt", "id": template.id}


@router.put("/{template_id}")
async def update_template(
    template_id: int,
    data: TemplateUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing zone template."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Nur Admins können Vorlagen bearbeiten")
    
    result = await db.execute(select(ZoneTemplate).where(ZoneTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")
    
    # If this is set as default, unset other defaults
    if data.is_default:
        all_templates = await db.execute(select(ZoneTemplate))
        for t in all_templates.scalars().all():
            if t.id == template_id:
                continue
            rec = t.records if isinstance(t.records, (list, dict)) else json.loads(t.records) if t.records else {}
            if rec.get("is_default"):
                rec["is_default"] = False
                t.records = rec
    
    current_records = template.records if isinstance(template.records, (list, dict)) else json.loads(template.records) if template.records else {}
    # Make a copy so SQLAlchemy detects the change
    new_records = dict(current_records)
    
    if data.name is not None:
        template.name = data.name
    if data.description is not None:
        template.description = data.description
    if data.nameservers is not None:
        new_records["nameservers"] = data.nameservers
    if data.kind is not None:
        new_records["kind"] = data.kind
    if data.soa_edit_api is not None:
        new_records["soa_edit_api"] = data.soa_edit_api
    if data.default_ttl is not None:
        new_records["default_ttl"] = data.default_ttl
    if data.records is not None:
        new_records["records"] = [r.model_dump() for r in data.records]
    if data.is_default is not None:
        new_records["is_default"] = data.is_default
    
    # Reassign to trigger SQLAlchemy change detection
    template.records = new_records
    flag_modified(template, "records")
    await db.commit()
    
    return {"message": f"Vorlage '{template.name}' aktualisiert"}


@router.delete("/{template_id}")
async def delete_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a zone template."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Nur Admins können Vorlagen löschen")
    
    result = await db.execute(select(ZoneTemplate).where(ZoneTemplate.id == template_id))
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")
    
    name = template.name
    await db.delete(template)
    await db.commit()
    
    return {"message": f"Vorlage '{name}' gelöscht"}
