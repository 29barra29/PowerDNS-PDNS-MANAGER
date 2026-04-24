"""ACME / DNS-01 Automation API.

Zwei oeffentliche Endpunkte fuer certbot (oder andere ACME-Clients), die per
Bearer-Token authentifiziert sind und ausschliesslich ``_acme-challenge.*``
TXT-Records in vorab gescopten Zonen schreiben/loeschen koennen.

Workflow:
1. Admin generiert einen Token im UI (Settings -> ACME / Auto-TLS) mit einer
   Liste erlaubter Zonen.
2. Auf dem Server wo certbot laeuft wird der Token + die URL des DNS-Managers
   in einer credentials-Datei hinterlegt.
3. certbot ruft ``--manual-auth-hook`` (= Aufruf an /api/v1/acme/present) und
   ``--manual-cleanup-hook`` (= /api/v1/acme/cleanup) auf, der Hook spricht
   diese Endpunkte mit ``curl`` an.

Sicherheit:
- Der Token kann *nur* TXT unter ``_acme-challenge.*`` und nur in den im
  Token-Scope eingetragenen Zonen anlegen/loeschen. Kein anderer Datenverkehr.
- Token-Plaintext wird nicht geloggt.
- ``last_used_at`` + ``last_used_ip`` werden bei jedem Aufruf aktualisiert,
  damit der Admin im UI verdaechtige Aktivitaet erkennt.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import AcmeToken, AuditLog
from app.services import acme as acme_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/acme", tags=["ACME"])


class AcmeChallengeRequest(BaseModel):
    """Body fuer present/cleanup. Felder bewusst kompatibel zu Lego/acme.sh:
    - ``domain``: FQDN (mit oder ohne Trailing-Dot), z.B. ``smtp.example.com``
    - ``validation``: Base64URL-Wert, den der ACME-Server erwartet
    """
    domain: str = Field(..., min_length=1, max_length=253, description="FQDN ohne _acme-challenge-Prefix")
    validation: str = Field(..., min_length=1, max_length=512, description="ACME-Validation-String (Base64URL)")


def _client_ip(request: Request) -> Optional[str]:
    """X-Forwarded-For respektieren (DNS-Manager laeuft typisch hinter Reverse-Proxy)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


async def _require_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> AcmeToken:
    """Validiert Authorization: Bearer <token>. Liefert die DB-Row zurueck.

    Generische 401-Antwort, damit ein Angreifer nicht an den vorhandenen Tokens
    durchprobieren kann (kein Hinweis auf "Token unbekannt vs. abgelaufen").
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer-Token fehlt",
            headers={"WWW-Authenticate": "Bearer"},
        )
    plaintext = authorization[7:].strip()
    token = await acme_service.verify_token(db, plaintext, remote_ip=_client_ip(request))
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungueltiger oder deaktivierter Token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def _audit(
    db: AsyncSession,
    *,
    token: AcmeToken,
    action: str,
    domain: str,
    status_value: str = "success",
    error: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """ACME-Operationen ins Audit-Log eintragen, damit man im UI sieht, welcher
    Token wann was gemacht hat. Das ``user_id`` Feld wird mit dem Ersteller des
    Tokens befuellt (kann None sein wenn Ersteller geloescht wurde)."""
    db.add(AuditLog(
        action=f"ACME_{action}",
        resource_type="acme",
        resource_name=domain,
        details={
            "token_id": token.id,
            "token_name": token.name,
            **(details or {}),
        },
        status=status_value,
        error_message=error,
        user_id=token.created_by_id,
    ))
    await db.flush()


@router.post("/present")
async def acme_present(
    body: AcmeChallengeRequest,
    request: Request,
    token: AcmeToken = Depends(_require_token),
    db: AsyncSession = Depends(get_db),
):
    """Legt den ``_acme-challenge.<domain>.`` TXT mit dem Validation-Wert an.

    Idempotent: mehrfacher Aufruf mit gleichem Wert ist OK.
    """
    try:
        result = await acme_service.present_challenge(
            db,
            fqdn=body.domain,
            validation=body.validation,
            allowed_zones=token.allowed_zones or [],
        )
    except PermissionError as exc:
        await _audit(db, token=token, action="PRESENT", domain=body.domain,
                     status_value="error", error=str(exc))
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        await _audit(db, token=token, action="PRESENT", domain=body.domain,
                     status_value="error", error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc))

    await _audit(db, token=token, action="PRESENT", domain=body.domain,
                 details={"zone": result["zone"]})
    return result


@router.post("/cleanup")
async def acme_cleanup(
    body: AcmeChallengeRequest,
    request: Request,
    token: AcmeToken = Depends(_require_token),
    db: AsyncSession = Depends(get_db),
):
    """Entfernt den Validation-Wert wieder. Nicht-existente Records sind OK."""
    try:
        result = await acme_service.cleanup_challenge(
            db,
            fqdn=body.domain,
            validation=body.validation,
            allowed_zones=token.allowed_zones or [],
        )
    except PermissionError as exc:
        await _audit(db, token=token, action="CLEANUP", domain=body.domain,
                     status_value="error", error=str(exc))
        raise HTTPException(status_code=403, detail=str(exc))

    await _audit(db, token=token, action="CLEANUP", domain=body.domain,
                 details={"zone": result["zone"]})
    return result


@router.get("/whoami", include_in_schema=False)
async def acme_whoami(
    token: AcmeToken = Depends(_require_token),
):
    """Diagnose-Endpoint fuer Hook-Skripte: testet, ob der Token gueltig ist.

    Liefert Name + Scope zurueck - keine Secrets. Praktisch fuer den ersten
    ``curl``-Test ohne dass man wirklich einen Record anlegen muss.
    """
    return {
        "ok": True,
        "token_name": token.name,
        "allowed_zones": token.allowed_zones or [],
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
    }
