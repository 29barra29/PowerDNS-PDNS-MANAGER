"""Modell-Slots (Bauplan Regel 11).

Jede Datei ``<ws>.py`` (Workstream-Kuerzel, klein, ``-`` als ``_``) definiert NEUE Tabellen mit
``from app.core.database import Base``. ``app/models/__init__.py`` importiert alle Slots
automatisch; ``init_db`` legt sie per ``create_all`` an und prueft die Spalten
(``verify_mapped_columns``). Zusaetzliche SQL-Statements (Indizes, Datenmigrationen) gehoeren
nach ``app/core/migrations/<ws>.py``.

Regeln: keine Spalten an Bestandstabellen (die legt der Integrator an), Zeitstempel Python-seitig
als naive UTC (``app.core.timeutil.utcnow``), kein FK (Projektkonvention).
"""
