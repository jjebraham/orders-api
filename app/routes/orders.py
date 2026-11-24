from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from app import services
from app.db import SessionDep
from app.errors import AppError
from app.schemas import OrderCreate, OrderOut

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=OrderOut,
    responses={
        200: {"description": "Replayed: the same key was already used (see Idempotent-Replayed)"},
        409: {"description": "Not enough stock"},
    },
)
async def create_order(
    data: OrderCreate,
    session: SessionDep,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Unique key per logical order. Retries with the same key return the "
            "original order.",
        ),
    ] = None,
):
    if not idempotency_key or len(idempotency_key) > 255:
        raise AppError(
            400,
            "idempotency_key_required",
            "The Idempotency-Key header is required (1-255 characters)",
        )
    result = await services.reserve_order(session, data, idempotency_key)
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return OrderOut.model_validate(result.order)


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(order_id: int, session: SessionDep):
    order = await services.get_order(session, order_id)
    return OrderOut.model_validate(order)


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(order_id: int, session: SessionDep):
    order = await services.cancel_order(session, order_id)
    return OrderOut.model_validate(order)
