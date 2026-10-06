from __future__ import annotations

"""Opretter SEND EASY-work items fra Insubiz-skadelisten.

En skade tilføjes kun, når alle betingelser er opfyldt:

- Skadenummer er større end MINIMUM_SKADENUMMER.
- Status er ikke en af statusserne i EKSKLUDEREDE_STATUSSER.
- Undertype er Arbejdsulykke.
- Skadetype er Arbejdsskade.
- Skadens detailopslag har standardCase = True.
- Skade-id findes ikke allerede i den konfigurerede ATS-kø.

Work item-reference er skadens tekniske skade-id.
Alle konfigurerbare inputs importeres fra configuration.py.
"""

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from automation_server_client import Workqueue

from configuration import (
    BOX_SKADE_ID,
    BOX_SKADE_NR,
    BOX_SKATYPE,
    BOX_STANDARD_CASE,
    BOX_STATUS,
    BOX_UNDERTYPE,
    CLAIM_GROUP_ID,
    CREATED_YEAR_FROM,
    CREATED_YEAR_TO,
    CUSTOMER_ID,
    CUSTOMER_SEGMENTATION_1,
    CUSTOMER_SEGMENTATION_2,
    EKSKLUDEREDE_STATUSSER,
    FORVENTET_SKATYPE,
    FORVENTET_UNDERTYPE,
    INCIDENT_YEAR_FROM,
    INCIDENT_YEAR_TO,
    MINIMUM_SKADENUMMER,
    QUEUE_CHECK_COMPLETED,
    QUEUE_CHECK_IN_PROGRESS,
    QUEUE_CHECK_NEW,
    QUEUE_CHECK_PENDING_USER_ACTION,
    QUEUE_ID,
    QUEUE_LOOKBACK_START,
    SHOW_TREE_DATA,
    SKADE_ID_FELTER,
    SKADE_NR_FELTER,
    SKADER_LISTE_COLUMNS,
    SKADETYPE_FELTER,
    STANDARD_CASE_FELTER,
    STATUS_FELTER,
    STATUS_ID,
    UNDERTYPE_FELTER,
)
from q_haderslev_vbo.automation_server.ats_is_item_in_queue import (
    is_item_in_queue,
)
from q_haderslev_vbo.automation_server.ats_update_item_data import (
    update_item_data,
)
from q_insubiz.api.client import InsubizApiClient
from q_insubiz.functionality.skader import (
    SKADER_LISTE,
    hent_skade_via_id,
)
from q_insubiz.utils import normalize_positive_id


logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# PUBLIC FUNKTION
# ------------------------------------------------------------


async def populate_queue(
    *,
    workqueue: Workqueue,
    api_client: InsubizApiClient,
    debug: bool = False,
) -> None:
    """Henter, filtrerer og tilføjer SEND EASY-items til køen.

    main.py ejer browser, login og API-klient.
    Funktionen bruger den delte klient og lukker den ikke.
    Output: None. Godkendte skader tilføjes til workqueue.
    """
    if workqueue is None:
        raise ValueError(
            "workqueue må ikke være None."
        )

    if api_client is None:
        raise ValueError(
            "Insubiz API-klienten mangler."
        )

    if not isinstance(debug, bool):
        raise TypeError(
            "debug skal være True eller False."
        )

    queue_id = _hent_queue_id()
    _valider_ekskluderede_statusser()

    logger.info(
        "SEND EASY queue-mode startet med delt API-klient. "
        "Queue-id: %s. Debug: %s. "
        "Ekskluderede statusser: %s.",
        queue_id,
        debug,
        EKSKLUDEREDE_STATUSSER,
    )

    skader = await _hent_skadeliste(
        api_client=api_client,
    )

    antal_tilfoejet = 0
    antal_dubletter = 0
    antal_filtreret = 0
    antal_ekskluderet_status = 0
    antal_ikke_standard_case = 0
    antal_ugyldige = 0

    queue_lookup_end = _utc_timestamp()

    for row_number, skade in enumerate(
        skader,
        start=1,
    ):
        try:
            felter = _hent_skadefelter(
                skade=skade,
                row_number=row_number,
            )
        except (
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            antal_ugyldige += 1

            logger.warning(
                "Skaderækken kunne ikke valideres. "
                "Række: %s. Fejl: %s",
                row_number,
                error,
            )
            continue

        status = felter["status"]

        if _er_ekskluderet_status(
            status=status,
        ):
            antal_ekskluderet_status += 1

            logger.info(
                "Skaden tilføjes ikke, fordi status "
                "er ekskluderet. "
                "Række: %s. Skade-id: %s. "
                "Skadenummer: %s. Status: %r.",
                row_number,
                felter["skade_id"],
                felter["skade_nr"],
                status,
            )
            continue

        if not _skal_tilfoejes_fra_liste(
            skade_nr=felter["skade_nr"],
            status=status,
            undertype=felter["undertype"],
            skadetype=felter["skadetype"],
        ):
            antal_filtreret += 1

            logger.info(
                "Skaden opfylder ikke SEND EASY-listens "
                "øvrige filtre. "
                "Række: %s. Skade-id: %s. "
                "Skadenummer: %s. Undertype: %r. "
                "Skadetype: %r. Status: %r.",
                row_number,
                felter["skade_id"],
                felter["skade_nr"],
                felter["undertype"],
                felter["skadetype"],
                status,
            )
            continue

        skade_id = felter["skade_id"]
        item_reference = str(skade_id)

        if _findes_i_koe(
            queue_id=queue_id,
            item_reference=item_reference,
            queue_lookup_end=queue_lookup_end,
        ):
            antal_dubletter += 1

            logger.info(
                "Springer eksisterende SEND EASY-item over. "
                "Skade-id: %s. Skadenummer: %s.",
                skade_id,
                felter["skade_nr"],
            )
            continue

        try:
            skade_detaljer = await hent_skade_via_id(
                api_client=api_client,
                skade_id=skade_id,
            )

            standard_case = _hent_standard_case(
                skade=skade_detaljer,
            )

            detaljeret_status = (
                _hent_valgfri_status_fra_detaljer(
                    skade=skade_detaljer,
                )
            )

        except Exception as error:
            antal_ugyldige += 1

            logger.warning(
                "Skadens detaildata kunne ikke valideres. "
                "Skade-id: %s. Fejl: %s",
                skade_id,
                error,
            )
            continue

        if (
            detaljeret_status
            and _er_ekskluderet_status(
                status=detaljeret_status,
            )
        ):
            antal_ekskluderet_status += 1

            logger.info(
                "Skaden tilføjes ikke, fordi status i "
                "detailopslaget er ekskluderet. "
                "Skade-id: %s. Skadenummer: %s. "
                "Listestatus: %r. Detailstatus: %r.",
                skade_id,
                felter["skade_nr"],
                status,
                detaljeret_status,
            )
            continue

        if standard_case is not True:
            antal_ikke_standard_case += 1

            logger.info(
                "Skaden tilføjes ikke, fordi standardCase "
                "ikke er True. "
                "Skade-id: %s. Skadenummer: %s. "
                "standardCase: %r.",
                skade_id,
                felter["skade_nr"],
                standard_case,
            )
            continue

        data_json = _opret_work_item_data(
            skade_id=skade_id,
            skade_nr=felter["skade_nr"],
            undertype=felter["undertype"],
            skadetype=felter["skadetype"],
            status=(
                detaljeret_status
                or status
            ),
            standard_case=standard_case,
        )

        workqueue.add_item(
            data=data_json,
            reference=item_reference,
        )

        antal_tilfoejet += 1

        logger.info(
            "SEND EASY-item tilføjet. "
            "Skade-id: %s. Skadenummer: %s. "
            "Reference: %s. Status: %s.",
            skade_id,
            felter["skade_nr"],
            item_reference,
            detaljeret_status or status,
        )

        print(
            "Tilføjet til SEND EASY-kø: "
            f"Skade-id {skade_id!r}, "
            f"skadenummer {felter['skade_nr']!r}, "
            f"status {(detaljeret_status or status)!r}."
        )

    _udskriv_opsummering(
        antal_hentet=len(skader),
        antal_tilfoejet=antal_tilfoejet,
        antal_dubletter=antal_dubletter,
        antal_filtreret=antal_filtreret,
        antal_ekskluderet_status=(
            antal_ekskluderet_status
        ),
        antal_ikke_standard_case=(
            antal_ikke_standard_case
        ),
        antal_ugyldige=antal_ugyldige,
    )


# ------------------------------------------------------------
# QUEUE-BROWSERKONFIGURATION
# ------------------------------------------------------------




# ------------------------------------------------------------
# INSUBIZ
# ------------------------------------------------------------


async def _hent_skadeliste(
    *,
    api_client: Any,
) -> list[dict[str, Any]]:
    """Henter skadelisten via de konfigurerede API-parametre."""
    skader = await SKADER_LISTE(
        api_client=api_client,
        customer_id=CUSTOMER_ID,
        customer_segmentation_1=(
            CUSTOMER_SEGMENTATION_1
        ),
        customer_segmentation_2=(
            CUSTOMER_SEGMENTATION_2
        ),
        claim_group_id=CLAIM_GROUP_ID,
        status_id=STATUS_ID,
        created_year_from=CREATED_YEAR_FROM,
        created_year_to=CREATED_YEAR_TO,
        incident_year_from=INCIDENT_YEAR_FROM,
        incident_year_to=INCIDENT_YEAR_TO,
        show_tree_data=SHOW_TREE_DATA,
        columns=SKADER_LISTE_COLUMNS,
    )

    if not isinstance(skader, list):
        raise TypeError(
            "SKADER_LISTE returnerede et uventet format. "
            f"Modtog: {type(skader).__name__}."
        )

    logger.info(
        "Skadelisten blev hentet. Antal: %s.",
        len(skader),
    )

    if (
        skader
        and isinstance(
            skader[0],
            dict,
        )
    ):
        logger.info(
            "Kolonner i skadelisten: %s.",
            list(skader[0]),
        )

    return skader


def _hent_skadefelter(
    *,
    skade: dict[str, Any],
    row_number: int,
) -> dict[str, Any]:
    """Henter og validerer de nødvendige felter fra én skaderække."""
    if not isinstance(skade, dict):
        raise TypeError(
            "Skadelisten indeholder en ugyldig række. "
            f"Række: {row_number}. "
            f"Modtog: {type(skade).__name__}."
        )

    return {
        "skade_id": _hent_skade_id(
            skade=skade,
        ),
        "skade_nr": _hent_tekstvaerdi(
            skade=skade,
            feltnavne=SKADE_NR_FELTER,
            felttype="skadenummer",
        ),
        "undertype": _hent_tekstvaerdi(
            skade=skade,
            feltnavne=UNDERTYPE_FELTER,
            felttype="undertype",
        ),
        "skadetype": _hent_tekstvaerdi(
            skade=skade,
            feltnavne=SKADETYPE_FELTER,
            felttype="skadetype",
        ),
        "status": _hent_tekstvaerdi(
            skade=skade,
            feltnavne=STATUS_FELTER,
            felttype="status",
        ),
    }


def _hent_valgfri_status_fra_detaljer(
    *,
    skade: dict[str, Any],
) -> str:
    """Henter status fra skadeopslaget, hvis status kan findes."""
    if not isinstance(skade, dict):
        raise TypeError(
            "Skadedetaljerne skal være en dictionary. "
            f"Modtog: {type(skade).__name__}."
        )

    direkte_status = skade.get(
        "status.text"
    )

    if direkte_status is not None:
        return _normaliser_tekstvaerdi(
            value=direkte_status,
        )

    for field_name in (
        "status",
        "incidentStatus",
        "IncidentStatus",
    ):
        value = skade.get(field_name)

        if value is None:
            continue

        if isinstance(value, dict):
            value = (
                value.get("text")
                or value.get("name")
                or value.get("value")
                or value.get("description")
            )

        normalized_value = _normaliser_tekstvaerdi(
            value=value,
        )

        if normalized_value:
            return normalized_value

    return ""


# ------------------------------------------------------------
# FILTRERING
# ------------------------------------------------------------


def _valider_ekskluderede_statusser() -> None:
    """Validerer statusserne, der ikke må tilføjes til køen."""
    if not isinstance(
        EKSKLUDEREDE_STATUSSER,
        tuple,
    ):
        raise TypeError(
            "EKSKLUDEREDE_STATUSSER i configuration.py "
            "skal være en tuple."
        )

    if not EKSKLUDEREDE_STATUSSER:
        raise ValueError(
            "EKSKLUDEREDE_STATUSSER i configuration.py "
            "må ikke være tom."
        )

    normaliserede_statusser: set[str] = set()

    for index, status in enumerate(
        EKSKLUDEREDE_STATUSSER,
        start=1,
    ):
        if not isinstance(status, str):
            raise TypeError(
                "Alle værdier i EKSKLUDEREDE_STATUSSER "
                "skal være tekst. "
                f"Position: {index}. "
                f"Modtog: {type(status).__name__}."
            )

        normalized_status = _normaliser_tekst(
            status
        )

        if not normalized_status:
            raise ValueError(
                "EKSKLUDEREDE_STATUSSER må ikke "
                "indeholde tomme værdier. "
                f"Position: {index}."
            )

        if normalized_status in normaliserede_statusser:
            raise ValueError(
                "EKSKLUDEREDE_STATUSSER indeholder "
                "den samme status flere gange. "
                f"Status: {status!r}."
            )

        normaliserede_statusser.add(
            normalized_status
        )


def _er_ekskluderet_status(
    *,
    status: Any,
) -> bool:
    """Returnerer True for Afsluttet og Genoptaget."""
    normalized_status = _normaliser_tekst(
        status
    )

    return any(
        normalized_status
        == _normaliser_tekst(
            excluded_status
        )
        for excluded_status
        in EKSKLUDEREDE_STATUSSER
    )


def _skal_tilfoejes_fra_liste(
    *,
    skade_nr: str,
    status: str,
    undertype: str,
    skadetype: str,
) -> bool:
    """Returnerer True, når rækken opfylder alle listefiltre."""
    numerisk_skade_nr = (
        _normaliser_skadenummer_til_heltal(
            value=skade_nr,
        )
    )

    if numerisk_skade_nr <= MINIMUM_SKADENUMMER:
        return False

    if _er_ekskluderet_status(
        status=status,
    ):
        return False

    if not _tekster_er_ens(
        undertype,
        FORVENTET_UNDERTYPE,
    ):
        return False

    return _tekster_er_ens(
        skadetype,
        FORVENTET_SKATYPE,
    )


def _hent_standard_case(
    *,
    skade: dict[str, Any],
) -> bool:
    """Henter standardCase fra skadeopslaget."""
    if not isinstance(skade, dict):
        raise TypeError(
            "hent_skade_via_id returnerede ikke "
            "en dictionary. "
            f"Modtog: {type(skade).__name__}."
        )

    value = _hent_feltvaerdi(
        skade=skade,
        feltnavne=STANDARD_CASE_FELTER,
        felttype="standardCase",
    )

    normalized_value = _normaliser_bool(
        value=value,
    )

    if normalized_value is None:
        raise RuntimeError(
            "standardCase havde en ugyldig værdi. "
            f"Modtog: {value!r}."
        )

    return normalized_value


# ------------------------------------------------------------
# AUTOMATION SERVER
# ------------------------------------------------------------


def _hent_queue_id() -> int:
    """Validerer og returnerer queue-id fra configuration.py."""
    try:
        return normalize_positive_id(
            name="QUEUE_ID",
            value=QUEUE_ID,
        )

    except (
        TypeError,
        ValueError,
    ) as error:
        raise RuntimeError(
            "QUEUE_ID i configuration.py skal være "
            "et positivt heltal. "
            f"Modtog: {QUEUE_ID!r}."
        ) from error


def _findes_i_koe(
    *,
    queue_id: int,
    item_reference: str,
    queue_lookup_end: str,
) -> bool:
    """Kontrollerer om item-reference allerede findes i køen."""
    return is_item_in_queue(
        queue_id=queue_id,
        item_reference=item_reference,
        new=QUEUE_CHECK_NEW,
        in_progress=QUEUE_CHECK_IN_PROGRESS,
        completed=QUEUE_CHECK_COMPLETED,
        pending_user_action=(
            QUEUE_CHECK_PENDING_USER_ACTION
        ),
        start_datetime=QUEUE_LOOKBACK_START,
        end_datetime=queue_lookup_end,
        updated_at=False,
    )


def _opret_work_item_data(
    *,
    skade_id: int,
    skade_nr: str,
    undertype: str,
    skadetype: str,
    status: str,
    standard_case: bool,
) -> dict[str, Any]:
    """Opretter work item-data med domænefelterne i box."""
    data_json: dict[str, Any] = {}

    update_item_data(
        data_json,
        box_updates={
            BOX_SKADE_ID: skade_id,
            BOX_SKADE_NR: skade_nr,
            BOX_UNDERTYPE: undertype,
            BOX_SKATYPE: skadetype,
            BOX_STATUS: status,
            BOX_STANDARD_CASE: standard_case,
        },
        update=False,
    )

    box = data_json.get("box")

    if not isinstance(box, dict):
        raise TypeError(
            "update_item_data oprettede ikke en gyldig box. "
            f"Modtog: {type(box).__name__}."
        )

    return data_json


# ------------------------------------------------------------
# FELTOPSLAG
# ------------------------------------------------------------


def _hent_skade_id(
    *,
    skade: dict[str, Any],
) -> int:
    """Henter og validerer skadens tekniske id."""
    raw_value = _hent_feltvaerdi(
        skade=skade,
        feltnavne=SKADE_ID_FELTER,
        felttype="skade-id",
    )

    try:
        return normalize_positive_id(
            name="skade_id",
            value=raw_value,
        )

    except (
        TypeError,
        ValueError,
    ) as error:
        raise RuntimeError(
            "Skadelistens skade-id er ugyldigt. "
            f"Modtog: {raw_value!r}."
        ) from error


def _hent_tekstvaerdi(
    *,
    skade: dict[str, Any],
    feltnavne: Iterable[str],
    felttype: str,
) -> str:
    """Henter en obligatorisk feltværdi som tekst."""
    value = _hent_feltvaerdi(
        skade=skade,
        feltnavne=feltnavne,
        felttype=felttype,
    )

    if isinstance(value, dict):
        value = (
            value.get("text")
            or value.get("name")
            or value.get("value")
            or value.get("id")
        )

    normalized_value = _normaliser_tekstvaerdi(
        value=value,
    )

    if not normalized_value:
        raise RuntimeError(
            f"Feltet {felttype!r} indeholder "
            "ingen brugbar tekstværdi."
        )

    return normalized_value


def _hent_feltvaerdi(
    *,
    skade: dict[str, Any],
    feltnavne: Iterable[str],
    felttype: str,
) -> Any:
    """Henter en værdi via en samling mulige feltnavne."""
    aliaser = tuple(
        feltnavne
    )

    faktisk_feltnavn = _find_feltnavn(
        skade=skade,
        feltnavne=aliaser,
    )

    if faktisk_feltnavn is None:
        raise RuntimeError(
            "Skadelisten mangler et forventet felt. "
            f"Felttype: {felttype!r}. "
            f"Forventede et af: {list(aliaser)!r}. "
            f"Tilgængelige felter: {list(skade)!r}."
        )

    value = skade.get(
        faktisk_feltnavn
    )

    if value is None:
        raise RuntimeError(
            "Skadelisten indeholder en tom værdi. "
            f"Felttype: {felttype!r}. "
            f"Felt: {faktisk_feltnavn!r}."
        )

    return value


def _find_feltnavn(
    *,
    skade: dict[str, Any],
    feltnavne: Iterable[str],
) -> str | None:
    """Finder det faktiske feltnavn via kendte aliaser."""
    normaliserede_felter = {
        _normaliser_feltnavn(
            faktisk_feltnavn
        ): faktisk_feltnavn
        for faktisk_feltnavn in skade
        if isinstance(
            faktisk_feltnavn,
            str,
        )
    }

    for feltnavn in feltnavne:
        faktisk_feltnavn = (
            normaliserede_felter.get(
                _normaliser_feltnavn(
                    feltnavn
                )
            )
        )

        if faktisk_feltnavn is not None:
            return faktisk_feltnavn

    return None


# ------------------------------------------------------------
# NORMALISERING
# ------------------------------------------------------------


def _normaliser_feltnavn(
    value: str,
) -> str:
    """Normaliserer et feltnavn til robust sammenligning."""
    normalized_value = (
        str(value)
        .strip()
        .casefold()
    )

    for character in (
        "_",
        "-",
        ".",
        ":",
    ):
        normalized_value = (
            normalized_value.replace(
                character,
                " ",
            )
        )

    return " ".join(
        normalized_value.split()
    )


def _normaliser_tekst(
    value: Any,
) -> str:
    """Normaliserer tekst til robust sammenligning."""
    return " ".join(
        str(value or "")
        .strip()
        .split()
    ).casefold()


def _normaliser_tekstvaerdi(
    *,
    value: Any,
) -> str:
    """Returnerer en trimmet tekstværdi eller tom tekst."""
    if value is None:
        return ""

    return " ".join(
        str(value)
        .strip()
        .split()
    )


def _tekster_er_ens(
    value: Any,
    expected: Any,
) -> bool:
    """Sammenligner to tekster robust."""
    return (
        _normaliser_tekst(value)
        == _normaliser_tekst(expected)
    )


def _normaliser_bool(
    *,
    value: Any,
) -> bool | None:
    """Normaliserer kendte bool-formater fra Insubiz."""
    if isinstance(value, bool):
        return value

    if (
        isinstance(value, int)
        and not isinstance(value, bool)
    ):
        if value in {
            0,
            1,
        }:
            return bool(value)

        return None

    if isinstance(value, str):
        normalized_value = (
            value.strip().casefold()
        )

        if normalized_value in {
            "true",
            "1",
            "ja",
            "yes",
        }:
            return True

        if normalized_value in {
            "false",
            "0",
            "nej",
            "no",
        }:
            return False

    return None


def _normaliser_skadenummer_til_heltal(
    *,
    value: Any,
) -> int:
    """Normaliserer skadenummeret til minimumsfilteret."""
    if isinstance(value, bool):
        raise TypeError(
            "Skadenummeret må ikke være boolsk."
        )

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if not value.is_integer():
            raise RuntimeError(
                "Skadenummeret indeholder decimaler. "
                f"Modtog: {value!r}."
            )

        return int(value)

    normalized_value = "".join(
        character
        for character in str(value).strip()
        if character.isdigit()
    )

    if not normalized_value:
        raise RuntimeError(
            "Skadenummeret kunne ikke konverteres "
            "til et heltal. "
            f"Modtog: {value!r}."
        )

    return int(
        normalized_value
    )


def _utc_timestamp() -> str:
    """Returnerer aktuelt UTC-tidspunkt i ATS-format."""
    return (
        datetime.now(
            timezone.utc
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


# ------------------------------------------------------------
# OPSUMMERING
# ------------------------------------------------------------


def _udskriv_opsummering(
    *,
    antal_hentet: int,
    antal_tilfoejet: int,
    antal_dubletter: int,
    antal_filtreret: int,
    antal_ekskluderet_status: int,
    antal_ikke_standard_case: int,
    antal_ugyldige: int,
) -> None:
    """Udskriver queue-kørslens resultat."""
    logger.info(
        "SEND EASY-køoprettelse afsluttet. "
        "Hentet: %s. Tilføjet: %s. "
        "Dubletter: %s. Filtreret: %s. "
        "Ekskluderet pga. status: %s. "
        "Ikke standardCase: %s. Ugyldige: %s.",
        antal_hentet,
        antal_tilfoejet,
        antal_dubletter,
        antal_filtreret,
        antal_ekskluderet_status,
        antal_ikke_standard_case,
        antal_ugyldige,
    )

    print()
    print("=" * 80)
    print("SEND EASY, KØOPRETTELSE AFSLUTTET")
    print("=" * 80)
    print(
        f"Antal hentet: {antal_hentet}"
    )
    print(
        f"Antal tilføjet: {antal_tilfoejet}"
    )
    print(
        f"Antal dubletter: {antal_dubletter}"
    )
    print(
        f"Antal filtreret fra: {antal_filtreret}"
    )
    print(
        "Antal fravalgt pga. status "
        "Afsluttet eller Genoptaget: "
        f"{antal_ekskluderet_status}"
    )
    print(
        "Antal fravalgt pga. standardCase: "
        f"{antal_ikke_standard_case}"
    )
    print(
        f"Antal ugyldige rækker: {antal_ugyldige}"
    )
    print("=" * 80)


__all__ = [
    "populate_queue",
]