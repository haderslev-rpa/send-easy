from __future__ import annotations

"""
Startpunkt for SEND EASY-robotten.

Ansvarsfordeling
----------------

main.py
    Opretter Automation Server-forbindelsen.
    Konfigurerer og opretter workqueue.
    Starter queue-mode eller process-mode.
    Opretter, genstarter og lukker BrowserSession.

config.py
    Indeholder QUEUE_ID og browserindstillinger.

populate_queue.py
    Henter relevante skader fra Insubiz og opretter
    work items.

behandel.py
    Behandler ét work item ad gangen.

Workqueue-konfiguration
-----------------------

AutomationServer.workqueue() læser workqueue_id fra den
konkrete AutomationServer-instans.

QUEUE_ID sættes derfor direkte på den samme instans,
umiddelbart før workqueue() kaldes.
"""

import asyncio
import logging
import sys
from pprint import pprint
from typing import Any

from automation_server_client import (
    AutomationServer,
    WorkItemError,
    WorkItemStatus,
    Workqueue,
)
from playwright.async_api import (
    Page,
)

from behandel import (
    behandel_page,
)
from config import (
    QUEUE_ID,
    get_headless,
)
from populate_queue import (
    populate_queue,
)
from q_haderslev_vbo.automation_server.ats_update_item_data import (
    update_item_data,
)
from q_haderslev_vbo.playwright.browser_session import (
    BrowserSession,
)


# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "[%(levelname)s] "
        "%(name)s: "
        "%(message)s"
    ),
)

logging.getLogger(
    "httpx"
).setLevel(
    logging.WARNING
)

logging.getLogger(
    "automation_server_client"
).setLevel(
    logging.WARNING
)

logging.getLogger(
    "playwright"
).setLevel(
    logging.WARNING
)

logging.getLogger(
    "debugpy"
).setLevel(
    logging.WARNING
)

logger = logging.getLogger(
    __name__
)


# ------------------------------------------------------------
# KOMMANDOLINJE
# ------------------------------------------------------------

def _har_argument(
    argument: str,
) -> bool:
    """Returnerer True, hvis argumentet er angivet."""
    if not isinstance(
        argument,
        str,
    ):
        raise TypeError(
            "argument skal være tekst."
        )

    normalized_argument = (
        argument.strip()
    )

    if not normalized_argument:
        raise ValueError(
            "argument må ikke være tomt."
        )

    return (
        normalized_argument
        in sys.argv[1:]
    )


def _get_debug_mode() -> bool:
    """Returnerer True, hvis --debug er angivet."""
    return _har_argument(
        "--debug"
    )


def _get_queue_mode() -> bool:
    """Returnerer True, hvis --queue er angivet."""
    return _har_argument(
        "--queue"
    )


# ------------------------------------------------------------
# QUEUE-ID
# ------------------------------------------------------------

def _valider_queue_id(
    queue_id: Any,
) -> int:
    """Validerer og returnerer workqueue-id."""
    if isinstance(
        queue_id,
        bool,
    ):
        raise TypeError(
            "QUEUE_ID må ikke være boolsk."
        )

    if isinstance(
        queue_id,
        int,
    ):
        normalized_queue_id = (
            queue_id
        )

    elif isinstance(
        queue_id,
        str,
    ):
        value = queue_id.strip()

        if not value.isdigit():
            raise ValueError(
                "QUEUE_ID skal være et positivt "
                "heltal. "
                f"Modtog: {queue_id!r}."
            )

        normalized_queue_id = int(
            value
        )

    else:
        raise TypeError(
            "QUEUE_ID skal være et heltal eller "
            "en numerisk tekstværdi. "
            f"Modtog: {type(queue_id).__name__}."
        )

    if normalized_queue_id <= 0:
        raise ValueError(
            "QUEUE_ID skal være større end 0. "
            f"Modtog: {normalized_queue_id}."
        )

    return normalized_queue_id


# ------------------------------------------------------------
# AUTOMATION SERVER
# ------------------------------------------------------------

def _opret_automation_server() -> AutomationServer:
    """Opretter Automation Server-klienten."""
    try:
        automation_server = (
            AutomationServer.from_environment()
        )

    except Exception as error:
        raise RuntimeError(
            "Automation Server kunne ikke "
            "initialiseres. "
            f"Fejl: {type(error).__name__}: {error}"
        ) from error

    if automation_server is None:
        raise RuntimeError(
            "AutomationServer.from_environment() "
            "returnerede None."
        )

    logger.info(
        "Automation Server-klienten blev oprettet."
    )

    return automation_server


def _konfigurer_workqueue_id(
    *,
    automation_server: AutomationServer,
    queue_id: int,
) -> None:
    """
    Sætter workqueue-id på Automation Server-instansen.

    Værdien kontrolleres efter assignment, så workqueue()
    ikke kaldes med en ukonfigureret instans.
    """
    if automation_server is None:
        raise ValueError(
            "automation_server må ikke være None."
        )

    normalized_queue_id = _valider_queue_id(
        queue_id
    )

    try:
        setattr(
            automation_server,
            "workqueue_id",
            normalized_queue_id,
        )

    except Exception as error:
        raise RuntimeError(
            "workqueue_id kunne ikke sættes på "
            "Automation Server-instansen. "
            f"Queue-id: {normalized_queue_id}. "
            f"Fejl: {type(error).__name__}: {error}"
        ) from error

    configured_queue_id = getattr(
        automation_server,
        "workqueue_id",
        None,
    )

    if configured_queue_id is None:
        raise RuntimeError(
            "Automation Server-instansen mangler "
            "workqueue_id efter konfiguration."
        )

    try:
        normalized_configured_queue_id = int(
            configured_queue_id
        )

    except (TypeError, ValueError) as error:
        raise RuntimeError(
            "Automation Server-instansens "
            "workqueue_id har et ugyldigt format. "
            f"Modtog: {configured_queue_id!r}."
        ) from error

    if (
        normalized_configured_queue_id
        != normalized_queue_id
    ):
        raise RuntimeError(
            "Automation Server-instansen blev "
            "konfigureret med et forkert "
            "workqueue-id. "
            f"Forventede: {normalized_queue_id}. "
            f"Modtog: "
            f"{normalized_configured_queue_id}."
        )

    logger.info(
        "Automation Server-kø konfigureret. "
        "Workqueue-id: %s.",
        normalized_configured_queue_id,
    )


def _opret_workqueue(
    *,
    automation_server: AutomationServer,
    queue_id: int,
) -> Workqueue:
    """
    Konfigurerer queue-id og opretter workqueue.

    Den samme AutomationServer-instans anvendes til både
    konfiguration og oprettelse.
    """
    normalized_queue_id = _valider_queue_id(
        queue_id
    )

    _konfigurer_workqueue_id(
        automation_server=automation_server,
        queue_id=normalized_queue_id,
    )

    configured_queue_id = getattr(
        automation_server,
        "workqueue_id",
        None,
    )

    logger.info(
        "Opretter workqueue. "
        "Forventet queue-id: %s. "
        "AutomationServer.workqueue_id: %r. "
        "AutomationServer-instans-id: %s.",
        normalized_queue_id,
        configured_queue_id,
        id(automation_server),
    )

    if not configured_queue_id:
        raise RuntimeError(
            "Automation Server mangler workqueue_id "
            "umiddelbart før workqueue() kaldes."
        )

    try:
        workqueue = (
            automation_server.workqueue()
        )

    except Exception as error:
        current_queue_id = getattr(
            automation_server,
            "workqueue_id",
            None,
        )

        raise RuntimeError(
            "Automation Server-workqueue kunne ikke "
            "oprettes. "
            f"Queue-id: {normalized_queue_id}. "
            "AutomationServer.workqueue_id ved fejl: "
            f"{current_queue_id!r}. "
            "AutomationServer-instans-id: "
            f"{id(automation_server)}. "
            f"Fejl: {type(error).__name__}: {error}"
        ) from error

    logger.info(
        "Workqueue oprettet. "
        "Queue-id: %s.",
        normalized_queue_id,
    )

    return workqueue


# ------------------------------------------------------------
# QUEUE-MODE
# ------------------------------------------------------------

async def _run_queue_mode(
    *,
    workqueue: Workqueue,
) -> None:
    """
    Rydder nye work items og fylder køen.

    populate_queue() modtager kun workqueue.
    """
    if workqueue is None:
        raise ValueError(
            "workqueue må ikke være None."
        )

    logger.info(
        "Queue-mode startet. "
        "Workqueue-id: %s.",
        QUEUE_ID,
    )

    try:
        workqueue.clear_workqueue(
            WorkItemStatus.NEW
        )

    except Exception as error:
        raise RuntimeError(
            "Nye work items kunne ikke ryddes "
            "fra workqueue. "
            f"Workqueue-id: {QUEUE_ID}. "
            f"Fejl: {type(error).__name__}: {error}"
        ) from error

    logger.info(
        "Eksisterende work items med status NEW "
        "er ryddet."
    )

    await populate_queue(
        workqueue=workqueue,
    )

    logger.info(
        "Queue-mode afsluttet. "
        "Workqueue-id: %s.",
        QUEUE_ID,
    )


# ------------------------------------------------------------
# PROCESS-MODE
# ------------------------------------------------------------

async def process_workqueue(
    *,
    workqueue: Workqueue,
    debug: bool,
) -> None:
    """Behandler køens work items ét ad gangen."""
    if workqueue is None:
        raise ValueError(
            "workqueue må ikke være None."
        )

    if not isinstance(
        debug,
        bool,
    ):
        raise TypeError(
            "debug skal være True eller False."
        )

    headless = get_headless()

    logger.info(
        "Process-mode startet. "
        "Workqueue-id: %s. "
        "Headless: %s. "
        "Debug: %s.",
        QUEUE_ID,
        headless,
        debug,
    )

    session, page = (
        await _opret_browser_session(
            headless=headless,
            debug=debug,
        )
    )

    try:
        for item in workqueue:
            with item:
                data = item.data

                try:
                    _udskriv_item(
                        item=item,
                        data=data,
                    )

                    await behandel_page(
                        item=item,
                        session=session,
                        page=page,
                    )

                    _faerdiggoer_item(
                        item=item,
                        data=data,
                    )

                except WorkItemError as error:
                    logger.error(
                        "WorkItemError for item %s: %s",
                        item.reference,
                        error,
                    )

                    item.fail(
                        str(error)
                    )

                    session, page = (
                        await _genstart_browser_session(
                            session=session,
                            headless=headless,
                            debug=debug,
                        )
                    )

                except Exception as error:
                    logger.exception(
                        "Uventet fejl ved behandling "
                        "af item %s.",
                        item.reference,
                    )

                    await _tag_screenshot_ved_fejl(
                        session=session,
                        error=error,
                    )

                    raise

    finally:
        await _luk_browser_session(
            session=session,
        )

    logger.info(
        "Process-mode afsluttet."
    )


# ------------------------------------------------------------
# BROWSERSESSION
# ------------------------------------------------------------

async def _opret_browser_session(
    *,
    headless: bool,
    debug: bool,
) -> tuple[BrowserSession, Page]:
    """Opretter browsersession og en ny side."""
    if not isinstance(
        headless,
        bool,
    ):
        raise TypeError(
            "headless skal være True eller False."
        )

    if not isinstance(
        debug,
        bool,
    ):
        raise TypeError(
            "debug skal være True eller False."
        )

    session = BrowserSession(
        headless=headless,
        debug=debug,
    )

    try:
        await session.start()

        page = await session.new_page()

    except Exception:
        try:
            await session.close()
        except Exception:
            logger.warning(
                "Browsersessionen kunne ikke lukkes "
                "efter en startfejl.",
                exc_info=True,
            )

        raise

    if page.is_closed():
        await session.close()

        raise RuntimeError(
            "Den oprettede Playwright-side er lukket."
        )

    logger.info(
        "Browsersession startet. "
        "Headless: %s. "
        "Debug: %s.",
        headless,
        debug,
    )

    return session, page


async def _genstart_browser_session(
    *,
    session: BrowserSession,
    headless: bool,
    debug: bool,
) -> tuple[BrowserSession, Page]:
    """Lukker den eksisterende session og starter en ny."""
    await _luk_browser_session(
        session=session,
    )

    new_session, new_page = (
        await _opret_browser_session(
            headless=headless,
            debug=debug,
        )
    )

    logger.info(
        "Browsersession genstartet."
    )

    return new_session, new_page


async def _luk_browser_session(
    *,
    session: BrowserSession,
) -> None:
    """Lukker browsersessionen kontrolleret."""
    if session is None:
        return

    try:
        await session.close()

    except Exception:
        logger.warning(
            "Browsersessionen kunne ikke "
            "lukkes korrekt.",
            exc_info=True,
        )


# ------------------------------------------------------------
# WORK ITEM
# ------------------------------------------------------------

def _udskriv_item(
    *,
    item: Any,
    data: Any,
) -> None:
    """Udskriver det næste work item."""
    print()
    print("=" * 80)
    print("NEXT ITEM")
    print("=" * 80)
    print(
        f"Reference: {item.reference}"
    )

    pprint(
        data
    )

    print("=" * 80)


def _faerdiggoer_item(
    *,
    item: Any,
    data: Any,
) -> None:
    """Opdaterer og afslutter et work item."""
    if not isinstance(
        data,
        dict,
    ):
        raise WorkItemError(
            "Work item data skal være en dictionary. "
            f"Modtog: {type(data).__name__}."
        )

    update_item_data(
        data,
        item=item,
        status="Completed",
        status_code="Færdig",
        state="Completed",
    )

    item.update(
        data
    )

    item.complete(
        "Completed"
    )

    logger.info(
        "Work item færdigbehandlet. "
        "Reference: %s.",
        item.reference,
    )


# ------------------------------------------------------------
# SCREENSHOT VED FEJL
# ------------------------------------------------------------

async def _tag_screenshot_ved_fejl(
    *,
    session: BrowserSession,
    error: Exception,
) -> None:
    """Forsøger at gemme screenshot ved en uventet fejl."""
    try:
        context = getattr(
            session,
            "context",
            None,
        )

        if context is None:
            logger.warning(
                "Screenshot kunne ikke tages, fordi "
                "browsersessionen ikke har en context."
            )
            return

        pages = context.pages

        if not pages:
            logger.warning(
                "Screenshot kunne ikke tages, fordi "
                "browsersessionen ikke har en aktiv side."
            )
            return

        page = pages[-1]

        if page.is_closed():
            logger.warning(
                "Screenshot kunne ikke tages, fordi "
                "den seneste side er lukket."
            )
            return

        await session.screenshot(
            page,
            (
                "hard_exception_"
                f"{type(error).__name__}"
            ),
            always=True,
        )

    except Exception:
        logger.warning(
            "Kunne ikke tage screenshot ved "
            "uventet fejl.",
            exc_info=True,
        )


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main() -> None:
    """Starter queue-mode eller process-mode."""
    debug = _get_debug_mode()
    queue_mode = _get_queue_mode()

    queue_id = _valider_queue_id(
        QUEUE_ID
    )

    automation_server = (
        _opret_automation_server()
    )

    workqueue = _opret_workqueue(
        automation_server=automation_server,
        queue_id=queue_id,
    )

    mode = (
        "queue"
        if queue_mode
        else "process"
    )

    logger.info(
        "Automation startet. "
        "Mode: %s. "
        "Workqueue-id: %s. "
        "Debug: %s.",
        mode,
        queue_id,
        debug,
    )

    if queue_mode:
        asyncio.run(
            _run_queue_mode(
                workqueue=workqueue,
            )
        )
        return

    asyncio.run(
        process_workqueue(
            workqueue=workqueue,
            debug=debug,
        )
    )


if __name__ == "__main__":
    main()