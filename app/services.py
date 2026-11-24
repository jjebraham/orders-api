"""Business logic for products and orders.

Stock safety rules:
* Every stock change happens inside one DB transaction.
* The product row is locked with SELECT ... FOR UPDATE before stock is read or changed,
  so concurrent orders for the same product run one after another.
* Orders are locked before their product when cancelling; creating an order only locks
  the product. Both paths lock in the same order, so they cannot deadlock each other.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import CANCELLED, RESERVED, Order, Product
from app.schemas import OrderCreate, ProductCreate

log = logging.getLogger("orders_api.orders")
_LOCK = {"populate_existing": True}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def create_product(session: AsyncSession, data: ProductCreate) -> Product:
    product = Product(name=data.name, price_cents=data.price_cents, stock=data.stock)
    session.add(product)
    await session.commit()
    return product


async def get_product(session: AsyncSession, product_id: int) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise AppError(404, "product_not_found", f"Product {product_id} does not exist")
    return product


async def list_products(
    session: AsyncSession, *, limit: int, offset: int, name: str | None
) -> tuple[list[Product], int]:
    query = select(Product)
    if name:
        query = query.where(Product.name.ilike(f"%{_escape_like(name)}%", escape="\\"))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(query.order_by(Product.id).limit(limit).offset(offset))
    return list(rows), total


def _request_hash(data: OrderCreate) -> str:
    return hashlib.sha256(f"{data.product_id}:{data.quantity}".encode()).hexdigest()


@dataclass(frozen=True)
class ReserveResult:
    order: Order
    replayed: bool


async def _find_by_key(session: AsyncSession, key: str) -> Order | None:
    return await session.scalar(select(Order).where(Order.idempotency_key == key))


def _check_same_request(order: Order, request_hash: str) -> ReserveResult:
    if order.request_hash != request_hash:
        raise AppError(
            422,
            "idempotency_key_reused",
            "This Idempotency-Key was already used with a different request body",
        )
    log.info("order replayed", extra={"order_id": order.id, "event": "order_replayed"})
    return ReserveResult(order=order, replayed=True)


async def reserve_order(
    session: AsyncSession, data: OrderCreate, idempotency_key: str
) -> ReserveResult:
    """Create an order and reserve its stock, or return the earlier order for the same key."""
    request_hash = _request_hash(data)

    # Fast path: this key was already processed. No locks needed.
    existing = await _find_by_key(session, idempotency_key)
    if existing is not None:
        await session.commit()
        return _check_same_request(existing, request_hash)

    try:
        product = (
            await session.scalars(
                select(Product)
                .where(Product.id == data.product_id)
                .with_for_update()
                .execution_options(**_LOCK)
            )
        ).first()
        if product is None:
            raise AppError(404, "product_not_found", f"Product {data.product_id} does not exist")

        # A concurrent request with the same key may have committed while we waited for the
        # lock. Check again now that we hold it.
        existing = await _find_by_key(session, idempotency_key)
        if existing is not None:
            await session.commit()
            return _check_same_request(existing, request_hash)

        if product.stock < data.quantity:
            available = product.stock  # read before rollback, which expires ORM attributes
            await session.rollback()
            log.info(
                "order rejected: insufficient stock",
                extra={"product_id": data.product_id, "event": "insufficient_stock"},
            )
            raise AppError(
                409,
                "insufficient_stock",
                f"Only {available} unit(s) left, requested {data.quantity}",
            )

        product.stock -= data.quantity
        order = Order(
            product_id=product.id,
            quantity=data.quantity,
            unit_price_cents=product.price_cents,
            status=RESERVED,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        session.add(order)
        await session.commit()
    except IntegrityError:
        # Only reachable if two requests with the same key slipped past the lock check.
        # The unique constraint caught it, so return the winner's order.
        await session.rollback()
        existing = await _find_by_key(session, idempotency_key)
        if existing is None:
            raise
        return _check_same_request(existing, request_hash)

    log.info(
        "order reserved",
        extra={"order_id": order.id, "product_id": product.id, "event": "order_reserved"},
    )
    return ReserveResult(order=order, replayed=False)


async def get_order(session: AsyncSession, order_id: int) -> Order:
    order = await session.get(Order, order_id)
    if order is None:
        raise AppError(404, "order_not_found", f"Order {order_id} does not exist")
    return order


async def cancel_order(session: AsyncSession, order_id: int) -> Order:
    """Cancel an order and release its stock. Cancelling twice releases stock only once."""
    order = (
        await session.scalars(
            select(Order).where(Order.id == order_id).with_for_update().execution_options(**_LOCK)
        )
    ).first()
    if order is None:
        raise AppError(404, "order_not_found", f"Order {order_id} does not exist")

    if order.status == CANCELLED:
        # Already done. Release the lock and return the current state.
        await session.commit()
        return order

    product = (
        await session.scalars(
            select(Product)
            .where(Product.id == order.product_id)
            .with_for_update()
            .execution_options(**_LOCK)
        )
    ).one()
    product.stock += order.quantity
    order.status = CANCELLED
    order.cancelled_at = datetime.now(UTC)
    await session.commit()
    log.info(
        "order cancelled",
        extra={"order_id": order.id, "product_id": product.id, "event": "order_cancelled"},
    )
    return order
