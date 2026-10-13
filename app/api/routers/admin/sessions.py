from __future__ import annotations

import logging

from fastapi import APIRouter

from app.api.dependencies import Admin
from app.api.schemas import (
    KfcAuthStateImportInput,
    KfcCaptureSaveInput,
    KfcSessionCreateInput,
    KfcSessionUpdateInput,
)
from app.service import kfc_account_service
from app.service.kfc.session_capture import capture_service
from app.errors import ok
from app.logging_utils import safe_json

router = APIRouter(prefix="/api/admin", tags=["admin-kfc-sessions"])
logger = logging.getLogger("uvicorn.error")


def _list():
    return ok({"items": kfc_account_service.list_kfc_sessions()})


def _create(body: KfcSessionCreateInput):
    return ok(kfc_account_service.create_kfc_session(body.model_dump()))


def _update(session_id: str, body: KfcSessionUpdateInput):
    return ok(kfc_account_service.update_kfc_session(session_id, body.model_dump(exclude_unset=True)))


def _delete(session_id: str):
    return ok(kfc_account_service.delete_kfc_session(session_id))


@router.get("/kfc-sessions")
def list_sessions(_: Admin):
    return _list()


@router.post("/kfc-sessions")
def create_session(body: KfcSessionCreateInput, _: Admin):
    return _create(body)


@router.patch("/kfc-sessions/{session_id}")
def update_session(session_id: str, body: KfcSessionUpdateInput, _: Admin):
    return _update(session_id, body)


@router.delete("/kfc-sessions/{session_id}")
def delete_session(session_id: str, _: Admin):
    return _delete(session_id)


@router.get("/kfc-tickets")
def list_tickets(_: Admin):
    return _list()


@router.post("/kfc-tickets")
def create_ticket(body: KfcSessionCreateInput, _: Admin):
    return _create(body)


@router.patch("/kfc-tickets/{session_id}")
def update_ticket(session_id: str, body: KfcSessionUpdateInput, _: Admin):
    return _update(session_id, body)


@router.delete("/kfc-tickets/{session_id}")
def delete_ticket(session_id: str, _: Admin):
    return _delete(session_id)


@router.post("/kfc-tickets/{session_id}/open-browser")
def open_ticket_browser(session_id: str, _: Admin):
    return ok(kfc_account_service.open_kfc_ticket_browser(session_id))


@router.post("/kfc-sessions/import-auth-state")
def import_auth_state(body: KfcAuthStateImportInput, _: Admin):
    return ok(kfc_account_service.import_kfc_auth_state(body.name, body.authState, body.remark))


@router.post("/kfc-sessions/capture/start")
def capture_start(_: Admin):
    result = capture_service.start()
    logger.info("KFC login capture API started captureId=%s", result.get("captureId"))
    return ok(result)


@router.post("/kfc-sessions/capture/{capture_id}/save")
def capture_save(capture_id: str, body: KfcCaptureSaveInput, _: Admin):
    captured_kbck = capture_service.get_kbck(capture_id)
    logger.info(
        "KFC login capture save requested captureId=%s kbckCaptured=%s",
        capture_id,
        bool(captured_kbck),
    )

    auth_state = capture_service.capture(capture_id)
    saved = kfc_account_service.import_kfc_auth_state(
        body.name,
        auth_state,
        body.remark,
        captured_kbck=captured_kbck,
    )
    local_storage = auth_state.get("localStorage") if isinstance(auth_state.get("localStorage"), dict) else {}
    logger.info(
        "KFC login capture saved captureId=%s ticketId=%s kbckCaptured=%s storage=%s",
        capture_id,
        saved.get("id"),
        bool(captured_kbck),
        safe_json(
            {
                "KEY_USER": local_storage.get("KEY_USER"),
                "DEVICE_ID": local_storage.get("DEVICE_ID"),
                "PERSISTENCE_SESSION_ID_BY_15MIN_preorder_13_0": local_storage.get(
                    "PERSISTENCE_SESSION_ID_BY_15MIN_preorder_13_0"
                ),
            }
        ),
    )
    capture_service.close(capture_id)
    return ok(saved)


@router.delete("/kfc-sessions/capture/{capture_id}")
def capture_cancel(capture_id: str, _: Admin):
    logger.info("KFC login capture cancel requested captureId=%s", capture_id)
    return ok(capture_service.close(capture_id))
