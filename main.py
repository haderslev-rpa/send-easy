"""Startpunkt for SEND EASY-robotten.

main.py ejer:
- Automation Server-forbindelsen og workqueue.
- BrowserSession, Playwright-side og Insubiz-login.
- Den delte Insubiz API-klient.
- Fejlhåndtering og afslutning af work items.

Forudsætninger:
- populate_queue() modtager workqueue, debug og api_client.
- behandel_page() modtager item, session, page og api_client.
- Ingen af disse funktioner opretter eller lukker API-klienten.

Konfiguration læses fortsat fra projektets eksisterende configuration.py.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from automation_server_client import (
    AutomationServer,
    WorkItemError,
    WorkItemStatus,
    Workqueue,
)
from playwright.async_api import Page

from behandel import behandel_page
from configuration import QUEUE_HEADLESS, QUEUE_ID, get_headless
from populate_queue import populate_queue

from q_haderslev_vbo.automation_server.ats_update_item_data import (
    update_item_data,
)
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from q_insubiz.api.client import InsubizApiClient
from q_insubiz.api_client import create_api_client_from_context
from q_insubiz.functionality.launch import launch_insubiz


# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

for logger_name in (
    "httpx",
    "automation_server_client",
    "playwright",
    "debugpy",
):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# KOMMANDOLINJE
# ------------------------------------------------------------

def _har_argument(argument: str) -> bool:
    """Returnerer True, hvis argumentet er angivet."""
    if not isinstance(argument, str):
        raise TypeError("argument skal være tekst.")

    normalized_argument = argument.strip()

    if not normalized_argument:
        raise ValueError("argument må ikke være tomt.")

    return normalized_argument in sys.argv[1:]


def _get_debug_mode() -> bool:
    """Returnerer True, hvis --debug er angivet."""
    return _har_argument("--debug")


def _get_queue_mode() -> bool:
    """Returnerer True, hvis --queue er angivet."""
    return _har_argument("--queue")


# ------------------------------------------------------------
# QUEUE-ID
# ------------------------------------------------------------

def _valider_queue_id(queue_id: Any) -> int:
    """Validerer og returnerer et positivt workqueue-id."""
    if isinstance(queue_id, bool):
        raise TypeError("QUEUE_ID må ikke være boolsk.")

    if isinstance(queue_id, int):
        normalized_queue_id = queue_id

    elif isinstance(queue_id, str):
        value = queue_id.strip()

        if not value.isdigit():
            raise ValueError(
                "QUEUE_ID skal være et positivt heltal. "
                f"Modtog: {queue_id!r}."
            )

        normalized_queue_id = int(value)

    else:
        raise TypeError(
            "QUEUE_ID skal være et heltal eller en numerisk tekstværdi. "
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
    """Opretter og returnerer Automation Server-klienten."""
    try:
        automation_server = AutomationServer.from_environment()
    except Exception as error:
        raise RuntimeError(
            "Automation Server kunne ikke initialiseres. "
            f"Fejl: {type(error).__name__}: {error}"
        ) from error

    if automation_server is None:
        raise RuntimeError(
            "AutomationServer.from_environment() returnerede None."
        )

    logger.info("Automation Server-klienten blev oprettet.")
    return automation_server


def _konfigurer_workqueue_id(
    *,
    automation_server: AutomationServer,
    queue_id: int,
) -> None:
    """Sætter og kontrollerer workqueue-id på ATS-instansen."""
    if automation_server is None:
        raise ValueError("automation_server må ikke være None.")

    normalized_queue_id = _valider_queue_id(queue_id)

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
        normalized_configured_queue_id = int(configured_queue_id)
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            "Automation Server-instansens workqueue_id "
            "har et ugyldigt format. "
            f"Modtog: {configured_queue_id!r}."
        ) from error

    if normalized_configured_queue_id != normalized_queue_id:
        raise RuntimeError(
            "Automation Server-instansen blev konfigureret "
            "med et forkert workqueue-id. "
            f"Forventede: {normalized_queue_id}. "
            f"Modtog: {normalized_configured_queue_id}."
        )

    logger.info(
        "Automation Server-kø konfigureret. Workqueue-id: %s.",
        normalized_configured_queue_id,
    )


def _opret_workqueue(
    *,
    automation_server: AutomationServer,
    queue_id: int,
) -> Workqueue:
    """Konfigurerer queue-id og returnerer workqueue."""
    normalized_queue_id = _valider_queue_id(queue_id)

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
        "Opretter workqueue. Forventet queue-id: %s. "
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
        workqueue = automation_server.workqueue()
    except Exception as error:
        current_queue_id = getattr(
            automation_server,
            "workqueue_id",
            None,
        )

        raise RuntimeError(
            "Automation Server-workqueue kunne ikke oprettes. "
            f"Queue-id: {normalized_queue_id}. "
            "AutomationServer.workqueue_id ved fejl: "
            f"{current_queue_id!r}. "
            f"AutomationServer-instans-id: {id(automation_server)}. "
            f"Fejl: {type(error).__name__}: {error}"
        ) from error

    logger.info(
        "Workqueue oprettet. Queue-id: %s.",
        normalized_queue_id,
    )

    return workqueue


# ------------------------------------------------------------
# BROWSER OG INSUBIZ
# ------------------------------------------------------------

async def _opret_browser_session(
    *,
    headless: bool,
    debug: bool,
) -> tuple[BrowserSession, Page, InsubizApiClient]:
    """Starter browseren, logger ind og opretter API-klienten.

    Output:
        BrowserSession, den indloggede side og en API-klient,
        som bruger den samme BrowserContext.
    """
    if not isinstance(headless, bool):
        raise TypeError("headless skal være True eller False.")

    if not isinstance(debug, bool):
        raise TypeError("debug skal være True eller False.")

    session = BrowserSession(
        headless=headless,
        debug=debug,
    )

    try:
        await session.start()
        page = await session.new_page()

        if page.is_closed():
            raise RuntimeError(
                "Den oprettede Playwright-side er lukket."
            )

        if session.context is None:
            raise RuntimeError(
                "BrowserSession mangler en BrowserContext."
            )

        logger.info(
            "Browsersession startet. Headless: %s. Debug: %s.",
            headless,
            debug,
        )

        # Login sker på den side, som BrowserSession ejer.
        await launch_insubiz(
            page=page,
            recorder=session.recorder,
        )

        # Denne factory starter ikke en ekstra browser.
        api_client = create_api_client_from_context(
            context=session.context,
            page=page,
        )

        logger.info(
            "Insubiz-login gennemført. "
            "API-klienten bruger BrowserSessions context."
        )

        return session, page, api_client

    except Exception as error:
        logger.exception(
            "Browseren eller Insubiz-sessionen kunne ikke startes."
        )

        await _tag_screenshot_ved_fejl(
            session=session,
            error=error,
        )

        await _luk_browser_session(
            session=session,
            api_client=None,
        )

        raise


async def _genstart_browser_session(
    *,
    session: BrowserSession,
    api_client: InsubizApiClient,
    headless: bool,
    debug: bool,
) -> tuple[BrowserSession, Page, InsubizApiClient]:
    """Lukker sessionen og opretter browser, login og klient igen."""
    await _luk_browser_session(
        session=session,
        api_client=api_client,
    )

    result = await _opret_browser_session(
        headless=headless,
        debug=debug,
    )

    logger.info(
        "Browsersession og Insubiz API-klient genstartet."
    )

    return result


async def _luk_browser_session(
    *,
    session: BrowserSession | None,
    api_client: InsubizApiClient | None,
) -> None:
    """Lukker API-adapteren og derefter browsersessionen.

    BrowserSession ejer og lukker selve BrowserContexten.
    """
    try:
        if api_client is not None:
            try:
                await api_client.close()
            except Exception:
                logger.warning(
                    "Insubiz API-klienten kunne ikke lukkes korrekt.",
                    exc_info=True,
                )
    finally:
        if session is not None:
            try:
                await session.close()
            except Exception:
                logger.warning(
                    "Browsersessionen kunne ikke lukkes korrekt.",
                    exc_info=True,
                )


# ------------------------------------------------------------
# QUEUE-MODE
# ------------------------------------------------------------

async def _run_queue_mode(
    *,
    workqueue: Workqueue,
    debug: bool,
) -> None:
    """Starter Insubiz-sessionen og fylder køen.

    Den eksisterende rydning af NEW-items er bevaret.
    Browserstart og login udføres dog før rydningen.
    """
    if workqueue is None:
        raise ValueError("workqueue må ikke være None.")

    if not isinstance(debug, bool):
        raise TypeError("debug skal være True eller False.")

    if not isinstance(QUEUE_HEADLESS, bool):
        raise TypeError("QUEUE_HEADLESS skal være True eller False.")

    logger.info(
        "Queue-mode startet. Workqueue-id: %s. "
        "Headless: %s. Debug: %s.",
        QUEUE_ID,
        QUEUE_HEADLESS,
        debug,
    )

    session, page, api_client = await _opret_browser_session(
        headless=QUEUE_HEADLESS,
        debug=debug,
    )

    try:
        # ADVARSEL: Dette sletter eksisterende NEW-items.
        # Linjen er bevaret fra den oprindelige proces.
        try:
            workqueue.clear_workqueue(WorkItemStatus.NEW)
        except Exception as error:
            raise RuntimeError(
                "Nye work items kunne ikke ryddes fra workqueue. "
                f"Workqueue-id: {QUEUE_ID}. "
                f"Fejl: {type(error).__name__}: {error}"
            ) from error

        logger.info(
            "Eksisterende work items med status NEW er ryddet."
        )

        # populate_queue.py skal modtage den delte klient.
        await populate_queue(
            workqueue=workqueue,
            debug=debug,
            api_client=api_client,
        )

    except Exception as error:
        logger.exception("Queue-mode fejlede.")

        await _tag_screenshot_ved_fejl(
            session=session,
            error=error,
        )

        raise

    finally:
        await _luk_browser_session(
            session=session,
            api_client=api_client,
        )

    logger.info(
        "Queue-mode afsluttet. Workqueue-id: %s.",
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
    """Behandler køens items med én delt Insubiz-session."""
    if workqueue is None:
        raise ValueError("workqueue må ikke være None.")

    if not isinstance(debug, bool):
        raise TypeError("debug skal være True eller False.")

    headless = get_headless(debug=debug)

    logger.info(
        "Process-mode startet. Workqueue-id: %s. "
        "Headless: %s. Debug: %s.",
        QUEUE_ID,
        headless,
        debug,
    )

    session, page, api_client = await _opret_browser_session(
        headless=headless,
        debug=debug,
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

                    # behandel.py skal bruge denne klient,
                    # ikke oprette sin egen.
                    await behandel_page(
                        item=item,
                        session=session,
                        page=page,
                        api_client=api_client,
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

                    await _tag_screenshot_ved_fejl(
                        session=session,
                        error=error,
                    )

                    item.fail(str(error))

                    # Alle tre objekter udskiftes sammen.
                    session, page, api_client = (
                        await _genstart_browser_session(
                            session=session,
                            api_client=api_client,
                            headless=headless,
                            debug=debug,
                        )
                    )

                except Exception as error:
                    logger.exception(
                        "Uventet fejl ved behandling af item %s.",
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
            api_client=api_client,
        )

    logger.info("Process-mode afsluttet.")


# ------------------------------------------------------------
# WORK ITEM
# ------------------------------------------------------------

def _udskriv_item(
    *,
    item: Any,
    data: Any,
) -> None:
    """Udskriver item-reference uden hele sagens data."""
    print()
    print("=" * 80)
    print("NEXT ITEM")
    print("=" * 80)
    print(f"Reference: {item.reference}")
    print("=" * 80)


def _faerdiggoer_item(
    *,
    item: Any,
    data: Any,
) -> None:
    """Opdaterer og afslutter et work item."""
    if not isinstance(data, dict):
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

    item.update(data)
    item.complete("Completed")

    logger.info(
        "Work item færdigbehandlet. Reference: %s.",
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
    """Forsøger at gemme screenshot uden at skjule originalfejlen."""
    try:
        context = session.context

        if context is None:
            logger.warning(
                "Screenshot kunne ikke tages: "
                "browsersessionen har ingen context."
            )
            return

        aktive_sider = [
            page
            for page in context.pages
            if not page.is_closed()
        ]

        if not aktive_sider:
            logger.warning(
                "Screenshot kunne ikke tages: "
                "browsersessionen har ingen aktiv side."
            )
            return

        await session.screenshot(
            aktive_sider[-1],
            f"exception_{type(error).__name__}",
            always=True,
        )

    except Exception:
        logger.warning(
            "Kunne ikke tage screenshot ved fejl.",
            exc_info=True,
        )


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main() -> None:
    """Starter queue-mode eller process-mode."""
    debug = _get_debug_mode()
    queue_mode = _get_queue_mode()
    queue_id = _valider_queue_id(QUEUE_ID)

    automation_server = _opret_automation_server()

    workqueue = _opret_workqueue(
        automation_server=automation_server,
        queue_id=queue_id,
    )

    mode = "queue" if queue_mode else "process"

    logger.info(
        "Automation startet. Mode: %s. "
        "Workqueue-id: %s. Debug: %s.",
        mode,
        queue_id,
        debug,
    )

    if queue_mode:
        asyncio.run(
            _run_queue_mode(
                workqueue=workqueue,
                debug=debug,
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