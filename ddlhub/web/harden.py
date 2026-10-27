"""本地 Web 加固（设计 §6.3）——"只监听 localhost" 不等于安全：

1. Host 头白名单 → 反 DNS rebinding；
2. 非 GET：Origin / Sec-Fetch-Site 必须同源 → 拦浏览器里任意网页的跨站 form POST；
3. 会话 cookie（HttpOnly SameSite=Strict）：本地模式首次 GET 即下发，非 GET 必须携带；
4. LAN 模式：未认证一律跳 /login（token 一次性交换后写 cookie 并清掉 URL）。
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse

COOKIE_NAME = "ddlhub_session"


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))  # 不实际发包，仅取路由源地址
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


class HardenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, container):
        super().__init__(app)
        self.container = container

    def allowed_hosts(self) -> set[str]:
        port = self.container.settings.port
        hosts = {
            f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}",
            "127.0.0.1", "localhost", "[::1]",
        }
        if self.container.settings.lan:
            ip = lan_ip()
            if ip:
                hosts |= {f"{ip}:{port}", ip}
            hosts.add(socket.gethostname())
        return hosts

    def _authed(self, request: Request) -> bool:
        return request.cookies.get(COOKIE_NAME) == self.container.session_secret

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        allowed = self.allowed_hosts()
        if host not in allowed:
            return PlainTextResponse("forbidden: bad host", status_code=403)

        method = request.method.upper()
        safe = method in ("GET", "HEAD", "OPTIONS")
        path = request.url.path
        authed = self._authed(request)

        if not safe:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc not in allowed:
                return PlainTextResponse("forbidden: cross origin", status_code=403)
            site = request.headers.get("sec-fetch-site")
            if site and site not in ("same-origin", "none"):
                return PlainTextResponse("forbidden: cross site", status_code=403)
            if not authed:
                return PlainTextResponse("unauthorized: no session", status_code=401)

        if self.container.settings.lan and not authed and not path.startswith("/static") and path != "/login":
            if safe:
                return RedirectResponse("/login", status_code=302)
            return PlainTextResponse("unauthorized", status_code=401)

        response = await call_next(request)
        if safe and not authed and not path.startswith("/static"):
            response.set_cookie(
                COOKIE_NAME, self.container.session_secret,
                httponly=True, samesite="strict", path="/",
            )
        return response
