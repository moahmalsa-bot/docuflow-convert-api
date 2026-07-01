import json
import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.conversion_routes import router as conversion_router
from app.api.pdf_routes import router as pdf_router
from app.utils.files import ApiError, ConversionError


logger = logging.getLogger("docuflow")


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="DocuFlow API",
        version="1.0.0",
        description="Production-ready document conversion, PDF analysis, and AI-assisted PDF editing API.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(conversion_router)
    app.include_router(pdf_router)
    register_handlers(app)
    register_middleware(app)
    return app


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def structured_log(event: str, **fields) -> None:
    logger.info(json.dumps({"event": event, **fields}, default=str))


def register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            structured_log("request_failed", request_id=request_id, method=request.method, path=request.url.path)
            raise
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        structured_log(
            "request_completed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            elapsed_ms=elapsed_ms,
        )
        return response


def register_handlers(app: FastAPI) -> None:
    @app.exception_handler(ConversionError)
    async def conversion_error_handler(request: Request, exc: ConversionError) -> JSONResponse:
        structured_log("conversion_error", path=request.url.path, detail=str(exc))
        return error_response(422, "conversion_error", str(exc))

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        structured_log("api_error", path=request.url.path, code=exc.code, detail=exc.message)
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        structured_log("http_error", path=request.url.path, status_code=exc.status_code, detail=exc.detail)
        return error_response(exc.status_code, "http_error", exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        structured_log("validation_error", path=request.url.path, errors=exc.errors())
        return error_response(422, "validation_error", "Request validation failed", errors=exc.errors())

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        structured_log("unhandled_error", path=request.url.path, detail=str(exc))
        return error_response(500, "internal_error", "Unexpected server error")


def error_response(status_code: int, code: str, detail, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": detail,
            "error": {
                "code": code,
                "message": detail,
                **extra,
            },
        },
    )


app = create_app()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "DocuFlow API"}
