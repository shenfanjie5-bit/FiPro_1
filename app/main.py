from fastapi import FastAPI

from app.api.routes import router
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title='FiPro_1 API', version='0.1.0')
    app.include_router(router)
    return app


app = create_app()
