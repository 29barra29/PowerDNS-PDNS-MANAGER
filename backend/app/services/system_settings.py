"""Zentraler Zugriff auf ``system_settings`` (F5 §5.6, Bauplan A.7/B.2).

Alle neuen Settings (F9-F15) lesen und schreiben ausschliesslich ueber diese Helfer:
- Secret-Keys (``SECRET_SETTING_KEYS``: SMTP-Passwort, Captcha-Secret, OIDC/LDAP-Secrets,
  Metrik-Token) werden beim Schreiben verschluesselt und beim Lesen entschluesselt.
  Unlesbare Werte kommen als Platzhalter ``UNREADABLE`` zurueck (``== ""``, per
  ``secret_store.is_unreadable`` erkennbar) – das Lesen wirft nie.
- Schreiben macht nur ``flush``, nie ``commit`` (Commit macht die Request-Dependency).
- Bool-Werte werden als ``"true"``/``"false"`` gespeichert.
- Die "Behalten"-Semantik (None/Maske) entscheidet der Aufrufer (``core/secret_mask.py``);
  diese Helfer schreiben immer.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secrets import SECRET_SETTING_KEYS, decrypt_value, encrypt_value
from app.models.models import SystemSetting


def is_secret_setting(key: str) -> bool:
    return key in SECRET_SETTING_KEYS


def _label(key: str) -> str:
    return f"system_settings.{key}"


def _to_storage(key: str, value: object) -> Optional[str]:
    """Normalisiert und verschluesselt (Secret-Keys) einen Wert fuer die Spalte ``value``."""
    if value is None:
        return None
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif isinstance(value, (int, float)):
        value = str(value)
    elif not isinstance(value, str):
        raise TypeError(f"Setting {key}: Wert muss str, bool, Zahl oder None sein")
    if is_secret_setting(key):
        # wirft SecretsUnavailableError fuer den Platzhalter UNREADABLE (nie "" ueber einen
        # unlesbaren Chiffretext schreiben) bzw. wenn kein Schluessel verfuegbar ist
        return encrypt_value(value)
    return value


async def get_settings(db: AsyncSession, keys: Iterable[str]) -> dict[str, Optional[str]]:
    """Liest mehrere Keys; fehlende Keys fehlen im Ergebnis. Secret-Keys werden entschluesselt."""
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        return {}
    rows = (
        await db.execute(
            select(SystemSetting.key, SystemSetting.value).where(SystemSetting.key.in_(wanted))
        )
    ).all()
    out: dict[str, Optional[str]] = {}
    for key, value in rows:
        out[key] = decrypt_value(value, label=_label(key)) if is_secret_setting(key) else value
    return out


async def get_setting(db: AsyncSession, key: str, default: Optional[str] = None) -> Optional[str]:
    """Ein Key; nur ein fehlender Key bzw. NULL liefert ``default`` (unlesbar -> UNREADABLE)."""
    value = (await get_settings(db, [key])).get(key)
    return default if value is None else value


async def get_bool_setting(db: AsyncSession, key: str, default: bool) -> bool:
    """Fehlend/leer -> ``default``; sonst True genau dann, wenn der Wert "true" ist (Gross/klein egal)."""
    value = await get_setting(db, key)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() == "true"


async def set_settings(db: AsyncSession, values: Mapping[str, object]) -> None:
    """Upsert mehrerer Keys (ein SELECT, ein flush). Secret-Keys verschluesselt, kein Commit."""
    if not values:
        return
    stored = {key: _to_storage(key, value) for key, value in values.items()}
    existing = {
        row.key: row
        for row in (
            await db.execute(select(SystemSetting).where(SystemSetting.key.in_(list(stored))))
        ).scalars()
    }
    for key, value in stored.items():
        row = existing.get(key)
        if row is None:
            db.add(SystemSetting(key=key, value=value))
        else:
            row.value = value
    await db.flush()


async def set_setting(db: AsyncSession, key: str, value: object) -> None:
    """Upsert eines Keys (nur flush). Secret-Keys werden verschluesselt gespeichert."""
    await set_settings(db, {key: value})


async def delete_setting(db: AsyncSession, key: str) -> None:
    """Entfernt einen Key (fehlender Key ist kein Fehler). Kein Commit."""
    await db.execute(delete(SystemSetting).where(SystemSetting.key == key))
    await db.flush()
