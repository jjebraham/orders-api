from fastapi import APIRouter, Query, status

from app import services
from app.db import SessionDep
from app.schemas import ProductCreate, ProductOut, ProductPage

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProductOut)
async def create_product(data: ProductCreate, session: SessionDep):
    return await services.create_product(session, data)


@router.get("", response_model=ProductPage)
async def list_products(
    session: SessionDep,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1_000_000),
    name: str | None = Query(None, min_length=1, max_length=200, description="Substring match"),
):
    items, total = await services.list_products(session, limit=limit, offset=offset, name=name)
    return ProductPage(
        items=[ProductOut.model_validate(p) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: int, session: SessionDep):
    return await services.get_product(session, product_id)
