from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from src.routes import (
    data,
    escalations,
    evaluations,
    events,
    incidents,
    ingestion,
    knowledge,
    llm,
    messages,
    users,
)
from src.services.evaluations import recover_interrupted_runs
from src.services.store import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 characters")
    init_db()
    recover_interrupted_runs()
    yield


app = FastAPI(title="ServeWell Incident Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Ingestion-Key"],
)
for router in (
    users.router,
    incidents.router,
    messages.router,
    events.router,
    llm.router,
    ingestion.router,
    data.router,
    knowledge.router,
    escalations.router,
    evaluations.router,
):
    app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
