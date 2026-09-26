from fastapi import FastAPI

from app.config import Settings, get_settings
from app.errors import install_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Amnezia Panel", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings
    install_error_handlers(app)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    return app
