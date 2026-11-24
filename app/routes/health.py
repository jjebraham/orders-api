from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import SessionDep

router = APIRouter()


@router.get("/health", tags=["ops"])
async def health(request: Request, session: SessionDep):
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error", "database": "down"})
    return {"status": "ok", "database": "ok", "version": request.app.version}
