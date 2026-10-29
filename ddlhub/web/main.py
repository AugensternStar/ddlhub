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


class SecurityHeadersMiddleware:
    """SEC-06：最小安全响应头（CSP 收紧脚本源，压缩潜在 XSS 的影响面）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                for name, value in (
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy",
                     b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                     b"img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; "
                     b"base-uri 'none'"),
                ):
                    headers.append((name, value))
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(container: Container) -> FastAPI:
    # S-P3-2：LAN 模式关闭交互式 API 文档（减少认证后接口枚举面）
    app = FastAPI(title="DDLHub",
                  docs_url=None if container.settings.lan else "/docs",
                  redoc_url=None)
    container.templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    app.state.container = container

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(HardenMiddleware, container=container)
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
    app.include_router(ui_router)
    app.include_router(api_router)
    return app
