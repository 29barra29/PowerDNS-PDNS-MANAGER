"""Zentraler Audit-Log-Helfer fuer sicherheitsrelevante Aktionen ausserhalb der DNS-Router."""
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AuditLog


async def write_audit_detached(
    action: str,
    resource_type: str,
    resource_name: Optional[str] = None,
    *,
    user_id: Optional[int] = None,
    details: Optional[dict[str, Any]] = None,
    status: str = "error",
    error_message: Optional[str] = None,
    server_name: Optional[str] = None,
) -> None:
    """Schreibt einen Audit-Eintrag in einer EIGENEN Session und committet sofort.

    Noetig fuer Fehler-Eintraege: die Request-Session wird bei HTTPException zurueckgerollt,
    der Eintrag ueber den Fehlversuch soll aber erhalten bleiben.
    """
    from app.core.database import async_session
    try:
        async with async_session() as s:
            s.add(
                AuditLog(
                    action=action,
                    resource_type=resource_type,
                    resource_name=(resource_name or "")[:255] or None,
                    server_name=server_name,
                    details=details,
                    status=status,
                    error_message=error_message,
                    user_id=user_id,
                )
            )
            await s.commit()
    except Exception as exc:  # noqa: BLE001 - Audit darf den Request nicht zusaetzlich brechen
        import logging
        logging.getLogger(__name__).warning("Audit-Eintrag (%s) konnte nicht geschrieben werden: %s", action, exc)


async def write_audit(
    db: AsyncSession,
    action: str,
    resource_type: str,
    resource_name: Optional[str] = None,
    *,
    user_id: Optional[int] = None,
    details: Optional[dict[str, Any]] = None,
    status: str = "success",
    error_message: Optional[str] = None,
    server_name: Optional[str] = None,
) -> None:
    """Schreibt einen AuditLog-Eintrag (flush, Commit macht get_db).

    Fehler-Eintraege gehen in eine eigene Session, weil get_db bei einer folgenden
    HTTPException zurueckrollt.
    """
    if status != "success":
        await write_audit_detached(
            action, resource_type, resource_name, user_id=user_id, details=details,
            status=status, error_message=error_message, server_name=server_name,
        )
        return
    db.add(
        AuditLog(
            action=action,
            resource_type=resource_type,
            resource_name=(resource_name or "")[:255] or None,
            server_name=server_name,
            details=details,
            status=status,
            error_message=error_message,
            user_id=user_id,
        )
    )
    await db.flush()
