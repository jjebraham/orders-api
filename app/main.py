from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.errors import install_error_handlers
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.routes import health, orders, products


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await engine.dispose()

    app = FastAPI(
        title="Orders API",
        version="1.0.0",
        description="Products with stock, and orders that reserve it. Idempotent, race-safe.",
        lifespan=lifespan,
    )
    app.state.engine = engine
    # expire_on_commit=False lets us return ORM objects after commit without a lazy reload,
    # which is not allowed in async code.
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)

    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(products.router)
    app.include_router(orders.router)
    return app


app = create_app()
