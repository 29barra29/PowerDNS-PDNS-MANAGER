"""ORM-Modelle des PDNS Manager.

Kernmodelle stehen in ``models.py``. Ab Welle 0b liefern Feature-Workstreams neue Tabellen
als Modell-Slot ``app/models/ext/<ws>.py`` (Bauplan Regel 11): gleiche ``Base`` aus
``app.core.database``, keine Aenderung an ``models.py``. Dieses Paket importiert beim ersten
Import alle Slot-Module (alphabetisch, ohne ``_``-Praefix), damit ``Base.metadata`` vollstaendig
ist – ``init_db`` (create_all, ``verify_mapped_columns``) importiert deshalb ``app.models``.

Spalten an Bestandstabellen legt nur der Integrator an (Modell + Statement), nicht ein Slot.
"""
import importlib
import pkgutil

from app.models import models  # noqa: F401 - registriert die Kernmodelle an Base.metadata
from app.models import ext as _ext


def _import_ext_models() -> list[str]:
    """Importiert alle Modell-Slots ``app/models/ext/*.py`` (sortiert nach Dateiname)."""
    names = sorted(
        m.name for m in pkgutil.iter_modules(_ext.__path__)
        if not m.name.startswith("_") and not m.ispkg
    )
    for name in names:
        importlib.import_module(f"{_ext.__name__}.{name}")
    return names


EXT_MODEL_MODULES: list[str] = _import_ext_models()
