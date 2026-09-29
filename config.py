from __future__ import annotations

"""Central konfiguration til SEND EASY-processen.

Alle konfigurerbare værdier til populate_queue og behandel samles her.
Proceskode skal importere værdier og hjælpefunktioner fra dette modul.
"""

from datetime import date


# ------------------------------------------------------------
# AUTOMATION SERVER
# ------------------------------------------------------------
# Indsæt det konkrete numeriske id på SEND EASY-køen her.
# Værdien skal være et positivt heltal.
QUEUE_ID: int = 16

# Statusser, der skal medtages i dubletkontrollen.
QUEUE_CHECK_NEW: bool = True
QUEUE_CHECK_IN_PROGRESS: bool = True
QUEUE_CHECK_COMPLETED: bool = True
QUEUE_CHECK_PENDING_USER_ACTION: bool = True

# Starttidspunkt for ATS-dubletkontrollen.
# Format: ISO 8601 i UTC.
QUEUE_LOOKBACK_START: str = "2025-01-01T00:00:00Z"


# ------------------------------------------------------------
# INSUBIZ OG BROWSER
# ------------------------------------------------------------
# True skjuler browseren ved normal kørsel.
# Ved --debug tilsidesætter get_headless() værdien til False.
HEADLESS: bool = True

CUSTOMER_ID: int | None = None
CUSTOMER_SEGMENTATION_1: int = -1
CUSTOMER_SEGMENTATION_2: int = -1
CLAIM_GROUP_ID: int = 0
STATUS_ID: int = -2

CREATED_YEAR_FROM: int = 0
CREATED_YEAR_TO: int = date.today().year
INCIDENT_YEAR_FROM: int = date.today().year - 1
INCIDENT_YEAR_TO: int = date.today().year

SHOW_TREE_DATA: bool = False


# ------------------------------------------------------------
# SKADELISTENS KOLONNER
# ------------------------------------------------------------
SKADER_LISTE_COLUMNS: list[str] = [
    "Id",
    "IncidentNumberInternal",
    "IncidentType",
    "IncidentSubType",
    "IncidentStatus",
    "Created",
    "standardCase",
]


# ------------------------------------------------------------
# FILTRE
# ------------------------------------------------------------
# Indsæt samme minimumsskadenummer som i den eksisterende proces.
# Hvis alle positive skadenumre skal accepteres, behold værdien 0.
MINIMUM_SKADENUMMER: int = 0

AFSLUTTET_STATUS: str = "Afsluttet"
FORVENTET_UNDERTYPE: str = "Arbejdsulykke"
FORVENTET_SKATYPE: str = "Arbejdsskade"


# ------------------------------------------------------------
# MULIGE FELTNAVNE FRA INSUBIZ-EKSPORTEN
# ------------------------------------------------------------
SKADE_ID_FELTER: tuple[str, ...] = (
    "Id",
    "id",
    "Skade id",
    "SkadeId",
)

SKADE_NR_FELTER: tuple[str, ...] = (
    "IncidentNumberInternal",
    "incidentNumberInternal",
    "Skade nr",
    "Skadenummer",
)

UNDERTYPE_FELTER: tuple[str, ...] = (
    "IncidentSubType",
    "incidentSubType",
    "Undertype",
)

SKADETYPE_FELTER: tuple[str, ...] = (
    "IncidentType",
    "incidentType",
    "Skadetype",
)

STATUS_FELTER: tuple[str, ...] = (
    "IncidentStatus",
    "incidentStatus",
    "Status",
    "status",
)

STANDARD_CASE_FELTER: tuple[str, ...] = (
    "standardCase",
    "StandardCase",
    "standard_case",
)


# ------------------------------------------------------------
# WORK ITEM BOX-FELTER
# ------------------------------------------------------------
BOX_SKADE_ID: str = "skade_id"
BOX_SKADE_NR: str = "skade_nr"
BOX_UNDERTYPE: str = "undertype"
BOX_SKATYPE: str = "skadetype"
BOX_STATUS: str = "status"
BOX_STANDARD_CASE: str = "standard_case"


# ------------------------------------------------------------
# HJÆLPEFUNKTIONER
# ------------------------------------------------------------
def get_headless(debug: bool = False) -> bool:
    """Returnerer browserens headless-indstilling.

    Ved debug=True vises browseren, så processen kan fejlsøges.
    Ved debug=False anvendes HEADLESS fra denne konfiguration.
    """
    if not isinstance(debug, bool):
        raise TypeError("debug skal være True eller False.")

    if debug:
        return False

    if not isinstance(HEADLESS, bool):
        raise TypeError("HEADLESS skal være True eller False.")

    return HEADLESS


# ------------------------------------------------------------
# EKSPORTEREDE NAVNE
# ------------------------------------------------------------
__all__ = [
    "AFSLUTTET_STATUS",
    "BOX_SKADE_ID",
    "BOX_SKADE_NR",
    "BOX_SKATYPE",
    "BOX_STANDARD_CASE",
    "BOX_STATUS",
    "BOX_UNDERTYPE",
    "CLAIM_GROUP_ID",
    "CREATED_YEAR_FROM",
    "CREATED_YEAR_TO",
    "CUSTOMER_ID",
    "CUSTOMER_SEGMENTATION_1",
    "CUSTOMER_SEGMENTATION_2",
    "FORVENTET_SKATYPE",
    "FORVENTET_UNDERTYPE",
    "HEADLESS",
    "INCIDENT_YEAR_FROM",
    "INCIDENT_YEAR_TO",
    "MINIMUM_SKADENUMMER",
    "QUEUE_CHECK_COMPLETED",
    "QUEUE_CHECK_IN_PROGRESS",
    "QUEUE_CHECK_NEW",
    "QUEUE_CHECK_PENDING_USER_ACTION",
    "QUEUE_ID",
    "QUEUE_LOOKBACK_START",
    "SHOW_TREE_DATA",
    "SKADE_ID_FELTER",
    "SKADE_NR_FELTER",
    "SKADER_LISTE_COLUMNS",
    "SKADETYPE_FELTER",
    "STANDARD_CASE_FELTER",
    "STATUS_FELTER",
    "STATUS_ID",
    "UNDERTYPE_FELTER",
    "get_headless",
]
