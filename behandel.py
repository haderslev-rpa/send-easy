from __future__ import annotations

"""Behandler ét SEND EASY-work item via q-insubiz API'et.

Forløb
------

1. Hent skade-id og skadenummer fra work itemets box.
2. Hent den aktuelle skade fra Insubiz.
3. Hvis skaden har status Afsluttet:
   - registrér state "1.0 Sag er afsluttet manuelt"
   - returnér til main.py, som afslutter itemet som Completed
4. Hvis skaden ikke er afsluttet:
   - kald send_skade_til_easy()
   - registrér state "1.0 Sendt til EASY"
   - returnér til main.py, som afslutter itemet som Completed

BrowserSession, Page og InsubizApiClient oprettes og ejes af main.py.
behandel.py bruger den delte API-klient og lukker den ikke.
"""

import logging
from typing import Any

from automation_server_client import WorkItemError

from q_haderslev_vbo.automation_server.ats_update_item_data import (
    update_item_data,
)
from q_insubiz.api.client import InsubizApiClient
from q_insubiz.functionality.skader import (
    hent_skade_via_id,
    send_skade_til_easy,
)
from q_insubiz.models import SendSkadeTilEasyResultat


logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# FORRETNINGSVÆRDIER
# ------------------------------------------------------------

AFSLUTTET_STATUS = "Afsluttet"


# ------------------------------------------------------------
# STATES
# ------------------------------------------------------------

STATE_SAG_ALLEREDE_AFSLUTTET = (
    "1.0 Sag er afsluttet manuelt"
)

STATE_SENDT_TIL_EASY = (
    "1.0 Sendt til EASY"
)

AFSLUTTENDE_STATES = (
    STATE_SAG_ALLEREDE_AFSLUTTET,
    STATE_SENDT_TIL_EASY,
)


# ------------------------------------------------------------
# WORK ITEM-FELTER
# ------------------------------------------------------------

SKADE_ID_FELTER = (
    "Skade_id",
    "skade_id",
    "Skade id",
    "Skade-id",
)

SKADE_NR_FELTER = (
    "Skade_nr",
    "skade_nr",
    "Skade nr",
    "Skade nr.",
    "Skade-nr",
    "Skadenr",
    "Skadenr.",
)


# ------------------------------------------------------------
# PUBLIC BEHANDLINGSFUNKTION
# ------------------------------------------------------------


async def behandel_page(
    *,
    item: Any,
    session: Any,
    page: Any,
    api_client: InsubizApiClient,
) -> None:
    """Behandler ét SEND EASY-work item med main.py's delte Insubiz-klient.

    BrowserSession, Page og InsubizApiClient ejes og lukkes af main.py.
    session og page modtages efter processkabelonens kaldemønster, mens
    skadeopslag og EASY-afsendelse udføres gennem api_client.
    """
    del session, page

    if api_client is None:
        raise WorkItemError("Insubiz API-klienten mangler.")

    data = item.data

    if not isinstance(data, dict):
        raise WorkItemError(
            "Work item data skal være en dictionary. "
            f"Modtog: {type(data).__name__}."
        )

    box = _hent_box(
        data=data,
    )

    skade_id = _hent_skade_id_fra_box(
        box=box,
    )

    skade_nr = _hent_skade_nr_fra_box(
        box=box,
    )

    if _har_afsluttende_state(data=data):
        logger.info(
            "Itemet har allerede en afsluttende state. "
            "Skade-id: %s. Skade-nr.: %s.",
            skade_id,
            skade_nr,
        )
        return

    print()
    print("=" * 80)
    print("SEND EASY, BEHANDLING AF SKADE")
    print("=" * 80)
    print(f"Skade-id: {skade_id}")
    print(f"Skade-nr.: {skade_nr}")

    try:
        skade = await hent_skade_via_id(
            api_client=api_client,
            skade_id=skade_id,
        )

        status = _hent_status_tekst(
            skade=skade,
        )

        print(f"Aktuel skadestatus: {status!r}")

        if _tekster_er_ens(
            status,
            AFSLUTTET_STATUS,
        ):
            _registrer_state(
                data=data,
                item=item,
                state=STATE_SAG_ALLEREDE_AFSLUTTET,
            )

            logger.info(
                "Skaden var allerede afsluttet. "
                "Skade-id: %s. Skade-nr.: %s.",
                skade_id,
                skade_nr,
            )

            print("RESULTAT: Skaden er allerede afsluttet.")
            print(f"State: {STATE_SAG_ALLEREDE_AFSLUTTET}")
            print("Itemet kan afsluttes som Completed.")
            print("=" * 80)
            print()
            return

        resultat = await send_skade_til_easy(
            api_client=api_client,
            skade_id=skade_id,
        )

        _valider_easy_resultat(
            resultat=resultat,
            skade_id=skade_id,
            skade_nr=skade_nr,
        )

        _registrer_state(
            data=data,
            item=item,
            state=STATE_SENDT_TIL_EASY,
        )

        if resultat.sendt_nu:
            resultat_tekst = "Skaden blev sendt til EASY i denne kørsel."
        else:
            resultat_tekst = "Skaden var allerede sendt til EASY."

        logger.info(
            "%s Skade-id: %s. Skade-nr.: %s. "
            "EASY-status før: %s. EASY-reference: %s.",
            resultat_tekst,
            skade_id,
            skade_nr,
            resultat.easy_status_foer,
            resultat.easy_reference,
        )

        print(f"RESULTAT: {resultat_tekst}")
        print(f"EASY-status før: {resultat.easy_status_foer!r}")
        print(f"EASY-reference: {resultat.easy_reference!r}")
        print(f"Besked: {resultat.besked}")
        print(f"State: {STATE_SENDT_TIL_EASY}")
        print("Itemet kan afsluttes som Completed.")
        print("=" * 80)
        print()

    except WorkItemError:
        raise
    except Exception as error:
        logger.exception(
            "SEND EASY-behandlingen fejlede. "
            "Skade-id: %s. Skade-nr.: %s.",
            skade_id,
            skade_nr,
        )

        raise WorkItemError(
            "SEND EASY-behandlingen fejlede. "
            f"Skade-id: {skade_id}. "
            f"Skade-nr.: {skade_nr}. "
            f"Fejl: {type(error).__name__}: {error}"
        ) from error



# ------------------------------------------------------------
# EASY-RESULTAT
# ------------------------------------------------------------


def _valider_easy_resultat(
    *,
    resultat: SendSkadeTilEasyResultat,
    skade_id: int,
    skade_nr: str,
) -> None:
    """Validerer resultatet fra send_skade_til_easy()."""
    if not isinstance(
        resultat,
        SendSkadeTilEasyResultat,
    ):
        raise WorkItemError(
            "send_skade_til_easy returnerede et ugyldigt resultat. "
            f"Skade-id: {skade_id}. "
            f"Skade-nr.: {skade_nr}. "
            f"Modtog: {type(resultat).__name__}."
        )

    if resultat.skade_id != skade_id:
        raise WorkItemError(
            "EASY-resultatets skade-id matcher ikke work itemet. "
            f"Forventede: {skade_id}. "
            f"Modtog: {resultat.skade_id}."
        )

    if resultat.allerede_sendt == resultat.sendt_nu:
        raise WorkItemError(
            "EASY-resultatet er inkonsistent. Præcis ét af "
            "allerede_sendt og sendt_nu skal være True. "
            f"Skade-id: {skade_id}."
        )

    if resultat.sendt_nu and resultat.afsendelses_response is None:
        raise WorkItemError(
            "EASY-afsendelsen mangler et afsendelsesresponse. "
            f"Skade-id: {skade_id}."
        )

    if resultat.allerede_sendt and (
        resultat.afsendelses_response is not None
    ):
        raise WorkItemError(
            "EASY-resultatet indeholder et afsendelsesresponse, "
            "selv om skaden allerede var sendt. "
            f"Skade-id: {skade_id}."
        )


# ------------------------------------------------------------
# STATUS
# ------------------------------------------------------------


def _hent_status_tekst(
    *,
    skade: dict[str, Any],
) -> str:
    """Henter skadens aktuelle status som tekst."""
    if not isinstance(skade, dict):
        raise WorkItemError(
            "Skadesvaret skal være en dictionary. "
            f"Modtog: {type(skade).__name__}."
        )

    direkte_status = skade.get("status.text")

    if direkte_status is not None:
        return _normaliser_paakraevet_tekst(
            value=direkte_status,
            field_name="status.text",
        )

    status = skade.get("status")

    if status is None:
        status = skade.get("incidentStatus")

    if status is None:
        status = skade.get("IncidentStatus")

    if status is None:
        status_id = skade.get("statusId")

        if status_id == 3:
            return AFSLUTTET_STATUS

        raise WorkItemError(
            "Skadesvaret mangler en læsbar status. "
            f"Tilgængelige topfelter: {list(skade.keys())!r}."
        )

    if isinstance(status, dict):
        value = (
            status.get("text")
            or status.get("name")
            or status.get("value")
            or status.get("description")
        )

        if value is None:
            status_id = status.get("id")

            if status_id == 3:
                return AFSLUTTET_STATUS

            raise WorkItemError(
                "Skadens statusobjekt mangler en læsbar tekst. "
                f"Statusobjekt: {status!r}."
            )

        return _normaliser_paakraevet_tekst(
            value=value,
            field_name="status.text",
        )

    return _normaliser_paakraevet_tekst(
        value=status,
        field_name="status",
    )


# ------------------------------------------------------------
# STATE
# ------------------------------------------------------------


def _hent_states(
    *,
    data: dict[str, Any],
) -> list[Any]:
    """Returnerer work itemets validerede state-liste."""
    states = data.get("state", [])

    if states is None:
        states = []
        data["state"] = states

    if not isinstance(states, list):
        raise WorkItemError(
            "Work item-feltet state skal være en liste. "
            f"Modtog: {type(states).__name__}."
        )

    return states


def _har_afsluttende_state(
    *,
    data: dict[str, Any],
) -> bool:
    """Kontrollerer om itemet allerede har en afsluttende state."""
    return any(
        expected_state in str(existing_state)
        for existing_state in _hent_states(data=data)
        for expected_state in AFSLUTTENDE_STATES
    )


def _har_state(
    *,
    data: dict[str, Any],
    state: str,
) -> bool:
    """Kontrollerer om en konkret state allerede findes."""
    return any(
        state in str(existing_state)
        for existing_state in _hent_states(data=data)
    )


def _registrer_state(
    *,
    data: dict[str, Any],
    item: Any,
    state: str,
) -> None:
    """Registrerer state uden at oprette dubletter."""
    if _har_state(
        data=data,
        state=state,
    ):
        logger.info(
            "State findes allerede: %s.",
            state,
        )
        return

    update_item_data(
        data,
        item=item,
        state=state,
    )


# ------------------------------------------------------------
# WORK ITEM-DATA
# ------------------------------------------------------------


def _hent_box(
    *,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Returnerer work itemets box."""
    box = data.get("box")

    if not isinstance(box, dict):
        raise WorkItemError(
            "Work item-feltet box skal være en dictionary. "
            f"Modtog: {type(box).__name__}."
        )

    return box


def _hent_skade_id_fra_box(
    *,
    box: dict[str, Any],
) -> int:
    """Henter og validerer skade-id fra box."""
    field_name = _find_box_feltnavn(
        box=box,
        feltnavne=SKADE_ID_FELTER,
    )

    if field_name is None:
        raise WorkItemError(
            "Work itemets box mangler Skade_id. "
            f"Forventede et af: {list(SKADE_ID_FELTER)!r}. "
            f"Tilgængelige felter: {list(box.keys())!r}."
        )

    return _normaliser_positivt_heltal(
        value=box.get(field_name),
        field_name="box.Skade_id",
    )


def _hent_skade_nr_fra_box(
    *,
    box: dict[str, Any],
) -> str:
    """Henter og validerer skadenummer fra box."""
    field_name = _find_box_feltnavn(
        box=box,
        feltnavne=SKADE_NR_FELTER,
    )

    if field_name is None:
        raise WorkItemError(
            "Work itemets box mangler Skade_nr. "
            f"Forventede et af: {list(SKADE_NR_FELTER)!r}. "
            f"Tilgængelige felter: {list(box.keys())!r}."
        )

    return _normaliser_paakraevet_tekst(
        value=box.get(field_name),
        field_name="box.Skade_nr",
    )


def _find_box_feltnavn(
    *,
    box: dict[str, Any],
    feltnavne: tuple[str, ...],
) -> str | None:
    """Finder et faktisk box-feltnavn via aliaser."""
    normaliserede_felter = {
        _normaliser_feltnavn(field_name): field_name
        for field_name in box
        if isinstance(field_name, str)
    }

    for feltnavn in feltnavne:
        faktisk_feltnavn = normaliserede_felter.get(
            _normaliser_feltnavn(feltnavn)
        )

        if faktisk_feltnavn is not None:
            return faktisk_feltnavn

    return None


# ------------------------------------------------------------
# GENERELLE HJÆLPERE
# ------------------------------------------------------------


def _normaliser_positivt_heltal(
    *,
    value: Any,
    field_name: str,
) -> int:
    """Normaliserer en værdi til et positivt heltal."""
    if isinstance(value, bool):
        raise WorkItemError(
            f"{field_name} må ikke være boolsk."
        )

    if isinstance(value, int):
        result = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise WorkItemError(
                f"{field_name} indeholder decimaler. "
                f"Modtog: {value!r}."
            )
        result = int(value)
    elif isinstance(value, str):
        normalized_value = value.strip()

        if normalized_value.endswith(".0"):
            normalized_value = normalized_value[:-2]

        if not normalized_value.isdigit():
            raise WorkItemError(
                f"{field_name} har et ugyldigt format. "
                f"Modtog: {value!r}."
            )

        result = int(normalized_value)
    else:
        raise WorkItemError(
            f"{field_name} har et ugyldigt format. "
            f"Modtog: {type(value).__name__}."
        )

    if result <= 0:
        raise WorkItemError(
            f"{field_name} skal være større end 0. "
            f"Modtog: {result}."
        )

    return result


def _normaliser_paakraevet_tekst(
    *,
    value: Any,
    field_name: str,
) -> str:
    """Normaliserer en obligatorisk tekstværdi."""
    if value is None:
        raise WorkItemError(
            f"{field_name} mangler."
        )

    normalized_value = str(value).strip()

    if not normalized_value:
        raise WorkItemError(
            f"{field_name} må ikke være tom."
        )

    return normalized_value


def _normaliser_tekst(value: Any) -> str:
    """Normaliserer tekst til robust sammenligning."""
    return " ".join(
        str(value).strip().split()
    ).casefold()


def _tekster_er_ens(
    value: Any,
    expected: Any,
) -> bool:
    """Sammenligner to tekster robust."""
    return (
        _normaliser_tekst(value)
        == _normaliser_tekst(expected)
    )


def _normaliser_feltnavn(value: str) -> str:
    """Normaliserer et feltnavn til sammenligning."""
    normalized_value = str(value).strip().casefold()

    for character in ("_", "-", ".", ":"):
        normalized_value = normalized_value.replace(
            character,
            " ",
        )

    return " ".join(normalized_value.split())


__all__ = [
    "behandel_page",
]
