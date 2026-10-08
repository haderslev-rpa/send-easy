"""Startpunkt for kraenkende-handlinger.

main.py ejer:
- Automation Server-forbindelsen og workqueue.
- BrowserSession, Playwright-side og Insubiz-login.
- API-klienten, som bruger samme browsercontext.
- Fejlhåndtering, screenshots og oprydning.
- Afslutning af work items.

--queue fylder køen.
Uden --queue behandles køens items.
--debug styrer fejlsøgning, men er ikke en dry-run.

Alle procesindstillinger læses fra configuration.py.
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
from q_haderslev_vbo.automation_server.ats_update_item_data import (
    update_item_data,
)
from q_haderslev_vbo.playwright.browser_session import BrowserSession
from q_insubiz.api.client import InsubizApiClient
from q_insubiz.api_client import create_api_client_from_context
from q_insubiz.functionality.launch import launch_insubiz
from q_insubiz.utils import normalize_positive_id

import configuration
from behandel import MANUEL_STATE_PREFIX, behandel_page
from populate_queue import populate_queue


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
# KOMMANDOLINJE OG KONFIGURATION
# ------------------------------------------------------------

def _har_argument(argument: str) -> bool:
    """Returnerer True, hvis argumentet er angivet."""
    return argument in sys.argv[1:]


def _get_debug_mode() -> bool:
    """Returnerer True ved --debug."""
    return _har_argument("--debug")


def _get_queue_mode() -> bool:
    """Returnerer True ved --queue."""
    return _har_argument("--queue")


def _hent_queue_id() -> int:
    """Returnerer det validerede kø-id fra configuration.py."""
    return normalize_positive_id(
        name="QUEUE_ID",
        value=configuration.QUEUE_ID,
    )


def _hent_headless(*, debug: bool, queue_mode: bool) -> bool:
    """Returnerer browserindstillingen for den valgte kørsel."""
    if not isinstance(debug, bool):
        raise TypeError("debug skal være True eller False.")

    if not isinstance(queue_mode, bool):
        raise TypeError("queue_mode skal være True eller False.")

    if queue_mode:
        headless = configuration.QUEUE_HEADLESS
    elif debug:
        headless = configuration.DEBUG_HEADLESS
    else:
        headless = configuration.HEADLESS

    if not isinstance(headless, bool):
        raise TypeError("Headless-indstillingen skal være True eller False.")

    return headless


# ------------------------------------------------------------
# AUTOMATION SERVER
# ------------------------------------------------------------

def _opret_automation_server() -> AutomationServer:
    """Initialiserer og returnerer Automation Server-klienten."""
    try:
        automation_server = AutomationServer.from_environment()
    except Exception as error:
        raise RuntimeError(
            "Automation Server kunne ikke initialiseres."
        ) from error

    if automation_server is None:
        raise RuntimeError(
            "AutomationServer.from_environment() returnerede None."
        )

    return automation_server


def _opret_workqueue(
    *,
    automation_server: AutomationServer,
    queue_id: int,
) -> Workqueue:
    """Konfigurerer kø-id som i send-easy og returnerer workqueue."""
    automation_server.workqueue_id = queue_id

    configured_queue_id = normalize_positive_id(
        name="AutomationServer.workqueue_id",
        value=automation_server.workqueue_id,
    )

    if configured_queue_id != queue_id:
        raise RuntimeError(
            "Automation Server blev konfigureret med et forkert kø-id."
        )

    try:
        workqueue = automation_server.workqueue()
    except Exception as error:
        raise RuntimeError(
            f"Workqueue kunne ikke oprettes. Queue-id: {queue_id}."
        ) from error

    _valider_workqueue(workqueue)

    logger.info("Workqueue oprettet. Queue-id: %s.", queue_id)
    return workqueue


def _valider_workqueue(workqueue: Workqueue) -> None:
    """Kontrollerer at køen matcher processens dubletopslag."""
    if workqueue is None:
        raise ValueError("Automation Server returnerede ingen workqueue.")

    actual_id = normalize_positive_id(
        name="workqueue.id",
        value=workqueue.id,
    )

    expected_id = _hent_queue_id()

    if actual_id != expected_id:
        raise RuntimeError(
            f"ATS-kø {actual_id} matcher ikke QUEUE_ID={expected_id}."
        )


# ------------------------------------------------------------
# SCREENSHOT OG OPRYDNING
# ------------------------------------------------------------

async def _tag_screenshot_ved_fejl(
    *,
    session: BrowserSession | None,
    error: BaseException,
) -> None:
    """Forsøger screenshot uden at skjule den oprindelige fejl."""
    try:
        if session is None or session.context is None:
            logger.warning(
                "Screenshot kan ikke tages: ingen browsercontext."
            )
            return

        aktive_sider = [
            page
            for page in session.context.pages
            if not page.is_closed()
        ]

        if not aktive_sider:
            logger.warning(
                "Screenshot kan ikke tages: ingen aktiv side."
            )
            return

        await session.screenshot(
            aktive_sider[-1],
            f"exception_{type(error).__name__}",
            always=True,
        )
    except Exception:
        logger.warning(
            "Screenshot ved fejl kunne ikke gemmes.",
            exc_info=True,
        )


async def _luk_browser_session(
    *,
    session: BrowserSession | None,
    api_client: InsubizApiClient | None,
) -> None:
    """Lukker API-adapteren og derefter BrowserSession."""
    try:
        if api_client is not None:
            try:
                await api_client.close()
            except Exception:
                logger.warning(
                    "Insubiz API-klienten kunne ikke lukkes.",
                    exc_info=True,
                )
    finally:
        if session is not None:
            try:
                await session.close()
            except Exception:
                logger.warning(
                    "BrowserSession kunne ikke lukkes.",
                    exc_info=True,
                )


# ------------------------------------------------------------
# BROWSER OG INSUBIZ
# ------------------------------------------------------------

async def _opret_browser_session(
    *,
    headless: bool,
    debug: bool,
) -> tuple[BrowserSession, Page, InsubizApiClient]:
    """Returnerer session, indlogget side og delt API-klient."""
    if not isinstance(headless, bool):
        raise TypeError("headless skal være True eller False.")

    if not isinstance(debug, bool):
        raise TypeError("debug skal være True eller False.")

    session = BrowserSession(
        headless=headless,
        debug=debug,
    )
    api_client: InsubizApiClient | None = None

    try:
        logger.info(
            "Starter BrowserSession. Headless: %s. Debug: %s.",
            headless,
            debug,
        )

        await session.start()
        logger.info("Browseren er startet.")

        page = await session.new_page()

        if page.is_closed():
            raise RuntimeError("Den oprettede Playwright-side er lukket.")

        if session.context is None:
            raise RuntimeError("BrowserSession mangler en browsercontext.")

        if page.context is not session.context:
            raise RuntimeError(
                "Playwright-siden tilhører ikke BrowserSessions context."
            )

        if session.recorder is None:
            raise RuntimeError("BrowserSession mangler sin recorder.")

        logger.info("Starter Insubiz-login.")

        await launch_insubiz(
            page=page,
            recorder=session.recorder,
        )

        api_client = create_api_client_from_context(
            context=session.context,
            page=page,
        )

        logger.info(
            "Insubiz-login gennemført. UI og API deler browsercontext."
        )

        return session, page, api_client

    except BaseException as error:
        logger.exception(
            "Browseren eller Insubiz-sessionen kunne ikke startes."
        )

        await _tag_screenshot_ved_fejl(
            session=session,
            error=error,
        )
        await _luk_browser_session(
            session=session,
            api_client=api_client,
        )
        raise


# ------------------------------------------------------------
# QUEUE-MODE
# ------------------------------------------------------------

async def _run_queue_mode(
    *,
    workqueue: Workqueue,
    debug: bool,
) -> None:
    """Logger ind, rydder NEW-items og fylder køen med delt klient."""
    _valider_workqueue(workqueue)

    clear_new_items = configuration.CLEAR_NEW_ITEMS_BEFORE_POPULATE

    if not isinstance(clear_new_items, bool):
        raise TypeError(
            "CLEAR_NEW_ITEMS_BEFORE_POPULATE skal være True eller False."
        )

    headless = _hent_headless(
        debug=debug,
        queue_mode=True,
    )

    logger.info(
        "Queue-mode startet. Queue-id: %s. Headless: %s. Debug: %s.",
        _hent_queue_id(),
        headless,
        debug,
    )

    session, _page, api_client = await _opret_browser_session(
        headless=headless,
        debug=debug,
    )

    try:
        if clear_new_items:
            logger.info("Rydder eksisterende NEW-items.")

            workqueue.clear_workqueue(WorkItemStatus.NEW)

            logger.info("Eksisterende NEW-items er ryddet.")

        # Denne proces kræver queue_id og modtager ikke debug.
        await populate_queue(
            workqueue=workqueue,
            queue_id=_hent_queue_id(),
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

    logger.info("Queue-mode afsluttet.")


# ------------------------------------------------------------
# WORK ITEM-AFSLUTNING
# ------------------------------------------------------------

def _hent_manuel_state(*, data: dict[str, Any]) -> str | None:
    """Returnerer den registrerede manuelle state eller None."""
    states = data.get("state", [])

    if states is None:
        return None

    if not isinstance(states, list):
        raise WorkItemError("Work item-feltet state skal være en liste.")

    prefix = MANUEL_STATE_PREFIX.strip().casefold()

    if not prefix:
        raise RuntimeError("MANUEL_STATE_PREFIX må ikke være tom.")

    for existing_state in reversed(states):
        state_text = str(existing_state).strip()

        # State kan indeholde et tidsstempel foran procesbeskrivelsen.
        if prefix in state_text.casefold():
            return state_text

    return None


def _faerdiggoer_item(*, item: Any, data: Any) -> None:
    """Gemmer processtatus og afslutter kø-itemet."""
    if not isinstance(data, dict):
        raise WorkItemError("Work item data skal være en dictionary.")

    manuel_state = _hent_manuel_state(data=data)

    if manuel_state is not None:
        # Bevarer den manuelle 1.0-state fra behandel.py.
        update_item_data(
            data,
            item=item,
            status=configuration.STATUS_MANUEL,
            status_code=configuration.STATUS_CODE_MANUEL,
        )
        completion_message = configuration.STATUS_MANUEL
    else:
        update_item_data(
            data,
            item=item,
            status=configuration.STATUS_COMPLETED,
            status_code=configuration.STATUS_CODE_COMPLETED,
            state=configuration.STATUS_COMPLETED,
        )
        completion_message = configuration.STATUS_COMPLETED

    item.update(data)
    item.complete(completion_message)

    logger.info(
        "Work item afsluttet. Reference: %s. Processtatus: %s.",
        item.reference,
        completion_message,
    )


def _udskriv_item(*, item: Any) -> None:
    """Logger reference uden at udskrive hele sagens data."""
    logger.info("Behandler næste item. Reference: %s.", item.reference)


# ------------------------------------------------------------
# PROCESS-MODE
# ------------------------------------------------------------

async def process_workqueue(
    *,
    workqueue: Workqueue,
    debug: bool,
) -> None:
    """Behandler køens items med en delt Insubiz-session."""
    _valider_workqueue(workqueue)

    headless = _hent_headless(
        debug=debug,
        queue_mode=False,
    )

    logger.info(
        "Process-mode startet. Queue-id: %s. Headless: %s. Debug: %s.",
        _hent_queue_id(),
        headless,
        debug,
    )

    session, page, api_client = await _opret_browser_session(
        headless=headless,
        debug=debug,
    )

    try:
        for item in workqueue:
            # Efter en soft fejl oprettes alle tre objekter igen.
            if session is None:
                session, page, api_client = await _opret_browser_session(
                    headless=headless,
                    debug=debug,
                )

            with item:
                try:
                    _udskriv_item(item=item)

                    await behandel_page(
                        item=item,
                        session=session,
                        page=page,
                        api_client=api_client,
                    )

                    _faerdiggoer_item(
                        item=item,
                        data=item.data,
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

                    await _luk_browser_session(
                        session=session,
                        api_client=api_client,
                    )

                    # Ingen genbrug af side eller klient fra lukket context.
                    session = None
                    page = None
                    api_client = None

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
# MAIN
# ------------------------------------------------------------

def main() -> None:
    """Initialiserer ATS og starter producer eller worker."""
    debug = _get_debug_mode()
    queue_mode = _get_queue_mode()
    queue_id = _hent_queue_id()

    automation_server = _opret_automation_server()

    workqueue = _opret_workqueue(
        automation_server=automation_server,
        queue_id=queue_id,
    )

    logger.info(
        "Automation startet. Queue-mode: %s. Queue-id: %s. Debug: %s.",
        queue_mode,
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