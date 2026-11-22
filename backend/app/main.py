from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1.router import router
from app.core.config import settings
from app.core.database import SessionLocal, engine


app = FastAPI(
    title="AgentOS",
    version="0.1.0",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        return JSONResponse({"status": "ready"})
    except Exception:
        return JSONResponse({"status": "not_ready"}, status_code=503)


@app.on_event("shutdown")
async def shutdown() -> None:
    await engine.dispose()
