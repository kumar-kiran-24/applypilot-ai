"""Stage E live browser-view ticket and authenticated WebSocket relay.

Transport security:

* ticket issuance requires a normal bearer-authenticated API request and checks
  application ownership plus a genuinely live browser;
* the ticket is single use, short lived, bound to that user/session/application
  and stored only as a hash;
* the WebSocket checks the browser Origin against the configured frontend
  origins (and rejects a missing Origin in production);
* only one view may attach to one browser session, and the ticket is consumed
  atomically before the first frame is sent;
* input is an allow-list (pointer, wheel, editing/navigation keys, text), rate
  limited and injected with CDP ``Input`` commands. No arbitrary CDP method,
  cookie, storage state, URL or filesystem path is exposed.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from app.core.config import settings
from app.core.dependencies import get_authenticated_user
from app.core.logging import get_logger
from app.db.session import get_db
from app.integrations.browser.streaming import LiveBrowserView, ViewProtocolError
from app.models.user import User
from app.schemas.browser_view import BrowserViewTicketResponse
from app.schemas.common import ErrorResponse
from app.services.browser_session_service import BrowserSessionService
from app.services.browser_view_service import (
    BrowserViewTicketError,
    get_browser_view_service,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/browser-sessions", tags=["Browser automation"])

CurrentUser = Annotated[User, Depends(get_authenticated_user)]
Database = Annotated[AsyncSession, Depends(get_db)]
ApplicationId = Annotated[UUID, Path(description="Application UUID")]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}

#: Transport close codes the frontend distinguishes. A refused ticket never
#: reaches the frame stream, so the code only tells the client why.
_TICKET_CLOSE_CODES: dict[str, int] = {
    "invalid_ticket": 4401,
    "ticket_expired": 4401,
    "ticket_replayed": 4401,
    "browser_session_closed": 4404,
    "view_already_attached": 4409,
}


def _origin_allowed(origin: str | None) -> bool:
    """Allow only configured frontend origins; require Origin in production."""
    if not origin:
        return not settings.is_production
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(origin)
        normalized = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
    except ValueError:
        return False
    allowed = {
        candidate.rstrip("/").lower()
        for candidate in settings.BACKEND_CORS_ORIGINS
        if candidate
    }
    return normalized in allowed


@router.post(
    "/by-application/{application_id}/view-ticket",
    response_model=BrowserViewTicketResponse,
    summary="Create a single-use live-browser view ticket",
    description=(
        "Returns a short-lived ticket for the authenticated owner of a live "
        "browser session. The ticket is exchanged once for a WebSocket that "
        "streams the same Playwright page and forwards user input. No CDP port, "
        "cookie or storage state is exposed."
    ),
    responses=ERROR_RESPONSES,
)
async def create_view_ticket(
    application_id: ApplicationId,
    current_user: CurrentUser,
    db: Database,
) -> BrowserViewTicketResponse:
    service = BrowserSessionService(db)
    # Re-checks application ownership, session ownership and a live browser.
    row, _ = await service.require_live_handle(current_user, application_id)
    issued = await get_browser_view_service().issue(
        user_id=current_user.id,
        session_id=row.id,
        application_id=row.application_id,
    )
    return BrowserViewTicketResponse(
        browser_session_id=row.id,
        ticket=issued.ticket,
        websocket_path=f"{settings.API_V1_PREFIX}/browser-sessions/view/ws",
        expires_at=issued.expires_at,
    )


@router.websocket("/view/ws")
async def browser_view_websocket(websocket: WebSocket) -> None:
    """Stream the owned page and accept narrowly validated user input."""
    if not _origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return

    token = websocket.query_params.get("ticket") or ""
    service = get_browser_view_service()
    try:
        attachment = await service.consume(token)
    except BrowserViewTicketError as exc:
        # Accept first so a browser client can read the structured reason.
        await websocket.accept()
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {"type": "error", "code": exc.code, "message": exc.message}
            )
        with contextlib.suppress(Exception):
            await websocket.close(
                code=_TICKET_CLOSE_CODES.get(exc.code, 4401),
                reason=exc.code[:120],
            )
        return

    await websocket.accept()
    send_lock = asyncio.Lock()

    async def send(payload: dict[str, object]) -> None:
        async with send_lock:
            if websocket.client_state is WebSocketState.CONNECTED:
                await websocket.send_json(payload)

    view = LiveBrowserView(
        attachment.handle,
        send,
        input_per_second=settings.APPLICATION_BROWSER_VIEW_INPUT_PER_SECOND,
        max_seconds=settings.APPLICATION_BROWSER_VIEW_MAX_SECONDS,
        # Closing on expiry keeps the view and the persisted session in step,
        # even when no other API request observes the session timing out.
        session_alive=lambda: service.is_live(
            session_id=attachment.session_id, user_id=attachment.user_id
        ),
    )
    receive_task: asyncio.Task[str] | None = None
    try:
        await view.start()
        receive_task = asyncio.create_task(websocket.receive_text())
        while not view.closed:
            done, _ = await asyncio.wait({receive_task}, timeout=0.5)
            if receive_task not in done:
                continue
            raw = receive_task.result()
            receive_task = asyncio.create_task(websocket.receive_text())
            try:
                await view.handle_message(raw)
            except ViewProtocolError as exc:
                await send(
                    {"type": "error", "code": exc.code, "message": exc.message}
                )
                if exc.code != "input_rate_limited":
                    view.close_code = 4400
                    view.close_reason = exc.code
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - transport/browser failures
        logger.warning(
            "Live browser view failed",
            extra={"stage": "browser_view", "error_class": type(exc).__name__},
        )
        view.close_code = 1011
        view.close_reason = "view_failed"
    finally:
        if receive_task is not None:
            receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await receive_task
        await view.close()
        await service.release(attachment.session_id, attachment.view_id)
        with contextlib.suppress(Exception):
            if websocket.client_state is WebSocketState.CONNECTED:
                await websocket.close(
                    code=view.close_code or 1000,
                    reason=(view.close_reason or "")[:120],
                )


__all__ = ["router"]
