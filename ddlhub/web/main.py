"""FastAPI app 组装：harden 中间件 + 路由 + 模板/静态资源。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..core.runtime import Container
from .harden import HardenMiddleware
from .routes_api import router as api_router
from .routes_ui import router as ui_router

_WEB_DIR = Path(__file__).resolve().parent


def create_app(container: Container) -> FastAPI:
    app = FastAPI(title="DDLHub", docs_url="/docs", redoc_url=None)
    container.templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    app.state.container = container

    app.add_middleware(HardenMiddleware, container=container)
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
    app.include_router(ui_router)
    app.include_router(api_router)
    return app
