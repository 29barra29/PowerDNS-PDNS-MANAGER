"""Migrations-Slots (Bauplan Regel 11, A.4).

Jede Datei ``<ws>.py`` (ohne ``_``-Praefix) darf definieren:

- ``SCHEMA_STATEMENTS: list[str]`` – idempotente DDL (``ADD COLUMN IF NOT EXISTS``,
  ``CREATE [UNIQUE] INDEX IF NOT EXISTS``); laeuft nach der Kernliste aus ``core/database.py``.
- ``DATA_MIGRATIONS: list[tuple[str, list[str]]]`` – einmalige Datenmigrationen
  ``(name, [statements])``; Marker ``system_settings.key = "migration_<name>"``
  (``run_data_migration_once``). Der Name muss global eindeutig sein.

``init_db`` liest die Slots sortiert nach Dateiname. Doppelpunkte in Statements als ``\\:``
schreiben (sonst Bind-Parameter von ``sqlalchemy.text``).
"""
