"""Schutzregeln fuer Benutzerkonten (F2/F3 5.4), geteilt von der Benutzerverwaltung (``routers/auth.py``) und F10.

- ``count_active_admins`` / ``assert_keeps_active_admin``: Der letzte AKTIVE Admin kann weder deaktiviert noch
  herabgestuft werden. Deaktivierte Admins zaehlen nicht (Audit-Fund: ein inaktiver zweiter Admin hat 2.4.1 die
  Sperre ausgehebelt).
- ``email_taken``: E-Mail-Duplikate vor dem Flush erkennen (409 statt IntegrityError/500).
- ``ensure_local_account``: Passwort-Aktionen nur fuer lokale Konten. Externe Konten (F10, ``auth_source`` oidc/ldap)
  verwalten ihr Passwort beim Identitaetsanbieter. ``routers/auth.py`` haengt den Hook als
  ``_ensure_local_account`` an allen Passwort-Stellen ein.
- ``count_active_local_admins`` / ``assert_keeps_local_admin`` (F10 5.11): Notfallzugang – solange OIDC oder LDAP
  aktiv ist, bleibt mindestens ein aktiver LOKALER Admin (nicht verknuepfen, deaktivieren, herabstufen, loeschen).
  Aus ``services/sso_provisioning.py`` hierher verschoben (WS-F10-APP-BE); dort bleiben Aliase.

Die Rollenpruefungen hier betreffen das ZIEL-Konto, nicht den Aufrufer (daher kein ``is_effective_admin``).
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import User

EMAIL_TAKEN_DETAIL = "E-Mail wird bereits verwendet"
USERNAME_OR_EMAIL_TAKEN_DETAIL = "Benutzername oder E-Mail wird bereits verwendet"
LAST_ADMIN_DEMOTE_DETAIL = "Letzter aktiver Admin kann nicht herabgestuft werden"
LAST_ADMIN_DEACTIVATE_DETAIL = "Der letzte aktive Admin kann nicht deaktiviert werden"
LAST_LOCAL_ADMIN_DETAIL = "Mindestens ein aktiver lokaler Admin muss als Notfallzugang bestehen bleiben"
EXTERNAL_ACCOUNT_DETAIL = (
    "Für extern angemeldete Konten (SSO/LDAP) wird das Passwort beim Identitätsanbieter verwaltet"
)


def _is_admin_role(role: Optional[str]) -> bool:
    return (role or "") == "admin"


async def count_active_admins(db: AsyncSession, *, exclude_user_id: Optional[int] = None) -> int:
    """Anzahl der Konten mit Rolle ``admin`` UND ``is_active`` (optional ohne ``exclude_user_id``)."""
    q = select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
    if exclude_user_id is not None:
        q = q.where(User.id != exclude_user_id)
    return int((await db.execute(q)).scalar() or 0)


async def assert_keeps_active_admin(db: AsyncSession, target: User, *, new_role: str, new_active: bool) -> None:
    """400, wenn die Aenderung (Rolle/Aktiv-Status von ``target``) den letzten aktiven Admin entfernen wuerde.

    Keine Abfrage, wenn ``target`` kein aktiver Admin ist oder es nach der Aenderung bleibt.
    """
    is_active_admin_now = _is_admin_role(getattr(target, "role", None)) and bool(getattr(target, "is_active", False))
    stays_active_admin = _is_admin_role(new_role) and bool(new_active)
    if not is_active_admin_now or stays_active_admin:
        return
    if await count_active_admins(db, exclude_user_id=target.id) >= 1:
        return
    if not _is_admin_role(new_role):
        raise HTTPException(status_code=400, detail=LAST_ADMIN_DEMOTE_DETAIL)
    raise HTTPException(status_code=400, detail=LAST_ADMIN_DEACTIVATE_DETAIL)


async def email_taken(db: AsyncSession, email: Optional[str], *, exclude_user_id: Optional[int] = None) -> bool:
    """True, wenn ein ANDERES Konto diese E-Mail-Adresse nutzt (leere Adresse -> False)."""
    if not email:
        return False
    q = select(User.id).where(User.email == email)
    if exclude_user_id is not None:
        q = q.where(User.id != exclude_user_id)
    return (await db.execute(q.limit(1))).first() is not None


def is_local_account(user) -> bool:
    """Lokales Konto (``auth_source`` fehlt/leer gilt als ``local``)."""
    return (getattr(user, "auth_source", None) or "local") == "local"


def ensure_local_account(user) -> None:
    """400 fuer externe Konten (SSO/LDAP): deren Passwort verwaltet der Identitaetsanbieter."""
    if not is_local_account(user):
        raise HTTPException(status_code=400, detail=EXTERNAL_ACCOUNT_DETAIL)


async def count_active_local_admins(db: AsyncSession, *, exclude_user_id: Optional[int] = None) -> int:
    """Aktive lokale Admins (``role == admin``, ``is_active``, ``auth_source == local``), optional ohne ein Konto."""
    q = select(func.count()).select_from(User).where(
        User.role == "admin", User.is_active.is_(True), User.auth_source == "local",
    )
    if exclude_user_id is not None:
        q = q.where(User.id != exclude_user_id)
    return int((await db.execute(q)).scalar() or 0)


async def assert_keeps_local_admin(
    db: AsyncSession,
    target: User,
    *,
    new_role: Optional[str] = None,
    new_active: Optional[bool] = None,
    removing: bool = False,
) -> None:
    """Notfallzugang (F10 5.11): Solange OIDC oder LDAP aktiv ist, muss ein aktiver lokaler Admin bleiben.

    ``removing=True`` = Konto wird geloescht oder mit einem Anmeldedienst verknuepft. Keine Abfrage, wenn ``target``
    kein aktiver lokaler Admin ist oder es nach der Aenderung bleibt. 400 mit ``LAST_LOCAL_ADMIN_DETAIL`` sonst.
    """
    if not is_local_account(target) or not _is_admin_role(getattr(target, "role", None)) \
            or not bool(getattr(target, "is_active", False)):
        return
    effective_active = target.is_active if new_active is None else new_active
    stays = (not removing) and _is_admin_role(new_role or target.role) and bool(effective_active)
    if stays:
        return
    from app.services.sso_settings import load_sso_config  # lazy: sso_settings -> sso_provisioning -> user_guard

    cfg = await load_sso_config(db)
    if not (cfg.oidc.enabled or cfg.ldap.enabled):
        return
    if await count_active_local_admins(db, exclude_user_id=target.id) >= 1:
        return
    raise HTTPException(status_code=400, detail=LAST_LOCAL_ADMIN_DETAIL)
