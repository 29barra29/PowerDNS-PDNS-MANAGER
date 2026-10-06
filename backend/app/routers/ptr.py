"""PTR-Hilfsendpunkte fuer den Record-Dialog (F11 3.8, Bauplan B.13: ROUTER_ORDER 135).

- ``GET /ptr/config``: Admin-Default der PTR-Pflege und Anzahl beschreibbarer Reverse-Zonen des Aufrufers.
- ``GET /ptr/lookup?ip=&name=&server=``: Vorschau, was die PTR-Pflege fuer eine IP tun wuerde.

Beide mit ``get_current_user`` (auch Panel-Tokens, deren Zonen-Scope ``has_zone_access`` beachtet). Zonennamen und
aktuelle PTR-Inhalte nur mit Leserecht auf die Reverse-Zone [S5]; nur lesend (``DbRead``).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.core.database import DbRead
from app.models.models import User
from app.schemas.dyndns import PtrConfigResponse, PtrLookupResponse
from app.services import ptr as ptr_service
from app.services.system_settings import get_bool_setting

ROUTER_ORDER = 135

router = APIRouter(prefix="/ptr", tags=["PTR"])


@router.get("/config", response_model=PtrConfigResponse)
async def ptr_config(db: DbRead, current_user: User = Depends(get_current_user)):
    return PtrConfigResponse(
        auto_default=await get_bool_setting(db, ptr_service.KEY_AUTO_DEFAULT, False),
        reverse_zones_available=await ptr_service.reverse_zones_available(db, current_user),
    )


@router.get("/lookup", response_model=PtrLookupResponse)
async def ptr_lookup(
    db: DbRead,
    ip: str = Query(..., max_length=64),
    name: Optional[str] = Query(None, max_length=255),
    server: Optional[str] = Query(None, max_length=100),
    current_user: User = Depends(get_current_user),
):
    try:
        result = await ptr_service.lookup(db, current_user, ip, name, server)
    except ValueError:
        raise HTTPException(status_code=422, detail="Ungueltige IP-Adresse")
    return PtrLookupResponse(**result)
