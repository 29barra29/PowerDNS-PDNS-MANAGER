"""Request-Schemas der Panel-Token-Verwaltung (F14 3.3/3.4).

``PanelTokenCreate``: ohne die neuen Felder entsteht ein Token wie in 2.4.1 (alle Zonen des Besitzers, Lesen &
Schreiben, kein Ablauf, keine Admin-Freigabe) – bestehende API-Aufrufer bleiben kompatibel; die Oberflaeche setzt
eigene, restriktive Defaults (ausgewaehlte Zonen, 90 Tage).

``PanelTokenUpdate``: alle Felder optional. ``scope_zones`` und ``expires_in_days`` unterscheiden "weggelassen"
(= unveraendert) von "explizit null" (= alle Zonen bzw. kein Ablauf) ueber ``model_fields_set``; bei ``name``,
``is_active``, ``permission`` und ``allow_admin`` bedeutet ``null`` ebenfalls "unveraendert".
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Grenzen identisch zu services/panel_token.py (MAX_SCOPE_ZONES, MAX_EXPIRY_DAYS); dort ohne Pydantic-Import.
NAME_MAX = 100
ZONE_NAME_MAX = 255
SCOPE_ZONES_MAX = 500
EXPIRY_DAYS_MAX = 3650

ZoneName = Annotated[str, Field(max_length=ZONE_NAME_MAX)]


class PanelTokenCreate(BaseModel):
    """Anlage-Body (F14 3.3). Ohne neue Felder: alle Zonen, Lesen & Schreiben, kein Ablauf, keine Admin-Freigabe."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=NAME_MAX)
    scope_zones: Optional[list[ZoneName]] = Field(default=None, max_length=SCOPE_ZONES_MAX)
    permission: Literal["manage", "read"] = "manage"
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=EXPIRY_DAYS_MAX)
    allow_admin: bool = False


class PanelTokenUpdate(BaseModel):
    """Aenderungs-Body (F14 3.4). Fehlende Felder bleiben unveraendert (siehe Moduldocstring)."""

    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = Field(default=None, min_length=1, max_length=NAME_MAX)
    is_active: Optional[bool] = None
    scope_zones: Optional[list[ZoneName]] = Field(default=None, max_length=SCOPE_ZONES_MAX)
    permission: Optional[Literal["manage", "read"]] = None
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=EXPIRY_DAYS_MAX)
    allow_admin: Optional[bool] = None

    def given(self, field: str) -> bool:
        """True, wenn das Feld im Request stand (auch als ``null``)."""
        return field in self.model_fields_set
