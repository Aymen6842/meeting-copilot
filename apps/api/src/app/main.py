"""ASGI entry point served by uvicorn (`app.main:app`).

Liveness only for now. Task 3 replaces this with the app factory, settings, logging and a
`/health` that checks Postgres, Redis and object storage.
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    application = FastAPI(title="Meeting Copilot API")

    @application.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
