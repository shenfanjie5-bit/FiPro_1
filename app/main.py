import logging
from uuid import uuid4

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.api.routes import router
from app.core.logging import configure_logging
from app.db.session import initialize_database_schema


logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get('x-request-id') or uuid4().hex
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers['x-request-id'] = request_id
        logger.info('request.completed path=%s method=%s status=%s request_id=%s', request.url.path, request.method, response.status_code, request_id)
        return response


def create_app() -> FastAPI:
    configure_logging()
    get_settings()
    app = FastAPI(title='FiPro_1 API', version='0.1.0')
    app.add_middleware(RequestIDMiddleware)

    @app.on_event('startup')
    def _initialize_database() -> None:
        initialize_database_schema()

    app.include_router(router)
    return app


app = create_app()
