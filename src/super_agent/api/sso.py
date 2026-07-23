"""OAuth2 SSO 认证登录模块。

基于标准 Authorization Code 流程接入统一授权中心。
使用 SSO 签发的 Ruoyi JWT (HS512) 作为会话凭证，可选 Redis 验证。

流程:
  1. GET /auth/login → 重定向到授权中心 authorize URL
  2. 用户登录后 → 授权中心回调 /auth/callback?code=xxx
  3. 后端用 code 换 JWT → 将 JWT 存入 cookie → 重定向到前端
  4. 后续请求携带 cookie → 中间件校验 JWT 签名 → 可选: Redis 确认未注销
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from super_agent.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "sa_session"

# ── Redis connection (lazy, for SSO token validation) ──

_sso_redis = None


def _get_sso_redis():
    """Get or create SSO Redis connection (lazy init).

    支持通过 redis_password 字段配置认证密码，密码为空时使用直连 URL。
    """
    global _sso_redis
    if _sso_redis is None:
        import redis
        cfg = settings.sso
        url = cfg.redis_url
        if cfg.redis_password:
            # 将密码嵌入 URL: redis://:password@host:port/db
            if "://" in url:
                # 保留 scheme 和路径，替换认证信息
                scheme, rest = url.split("://", 1)
                # 去掉 rest 中可能已有的认证信息
                if "@" in rest:
                    rest = rest.split("@", 1)[1]
                url = f"{scheme}://:{cfg.redis_password}@{rest}"
        _sso_redis = redis.from_url(url, decode_responses=True)
    return _sso_redis


# ── JWT helpers ──


def _verify_jwt(token: str) -> dict | None:
    """Verify HS512 JWT and return claims.

    JJWT 的 signWith(String) / setSigningKey(String) 会把字符串当作 Base64 编码处理，
    先解码再作为 HMAC 密钥。PyJWT 则直接把字符串 encode 成 UTF-8 字节。
    所以这里需要先 Base64 解码 jwt_secret，保持与 JJWT 行为一致。

    !!! jwt_secret 建议使用 4 的倍数长度的 Base64 字符串（如 24/28/32 字符），
    避免 Java DatatypeConverter 丢弃尾部字符导致预期外的密钥截断。
    """
    try:
        secret = settings.sso.jwt_secret
        # 匹配 Java DatatypeConverter 行为：只处理完整 4 字符组，不足丢弃
        # "abcdefghijklmnopqrstuvwxyz"(26) → 只有前24字符 → 18字节
        n = len(secret) // 4 * 4
        key = base64.b64decode(secret[:n])
    except Exception as e:
        logger.warning("Failed to base64-decode jwt_secret: %s", e)
        key = settings.sso.jwt_secret.encode("utf-8")
    try:
        return pyjwt.decode(
            token,
            key,
            algorithms=["HS512"],
            options={"verify_exp": False},
        )
    except pyjwt.InvalidTokenError as e:
        logger.warning("JWT verification failed: %s", e)
        return None


def _check_token_redis(user_key: str) -> bool:
    """Check if token session still exists in SSO's Redis.

     在 Redis 中存 login_tokens:<uuid>，注销时删除该 key。
    Redis 不可用时降级为通过（不做验证）。
    """
    cfg = settings.sso
    if not cfg.redis_enabled:
        return True
    try:
        r = _get_sso_redis()
        key = f"{cfg.redis_key_prefix}{user_key}"
        return bool(r.exists(key))
    except Exception as e:
        logger.warning("SSO Redis check failed, allowing request: %s", e)
        return True


# ── OAuth2 helpers ──


def build_redirect_uri() -> str:
    return settings.sso.redirect_uri.rstrip("/")


def build_authorize_url() -> str:
    """Build the OAuth2 authorize URL to redirect users to."""
    base = settings.sso.auth_base_url.rstrip("/")
    params = {
        "client_id": settings.sso.client_id,
        "redirect_uri": build_redirect_uri(),
        "response_type": "code",
    }
    return f"{base}/auth/oauth2/authorize?{urlencode(params)}"


def exchange_code(code: str) -> dict | None:
    """Exchange authorization code for access token."""
    try:
        resp = httpx.post(
            f"{settings.sso.auth_base_url.rstrip('/')}/auth/oauth2/client/token",
            json={
                "code": code,
                "clientId": settings.sso.client_id,
                "clientSecret": settings.sso.client_secret,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("Token exchange failed: %s %s", resp.status_code, resp.text[:200])
            return None
        body = resp.json()
        # Ruoyi-style: R.ok(result) → {code: 200, msg: "success", data: {...}}
        if "data" in body:
            return body["data"]
        return body
    except Exception as e:
        logger.error("Token exchange error: %s", e)
        return None


def get_user_info(access_token: str) -> dict | None:
    """Get user info from auth server using access token."""
    try:
        resp = httpx.get(
            f"{settings.sso.auth_base_url.rstrip('/')}/system/user/getInfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("GetUserInfo failed: %s %s", resp.status_code, resp.text[:200])
            return None
        body = resp.json()
        # Ruoyi-style: R.ok(result) → {code: 200, msg: "success", data: {...}}
        if "data" in body:
            return body["data"]
        return body
    except Exception as e:
        logger.error("GetUserInfo error: %s", e)
        return None


# ── Auth Middleware ──────────────────


class SSOMiddleware(BaseHTTPMiddleware):
    """Validate JWT session cookie on every request and inject UserContext.

    JWT 验证流程:
      1. 从 cookie 或 Authorization header 读 token
      2. HS512 签名验证（pyjwt.decode）
      3. 可选: Redis EXISTS login_tokens:<user_key>（确认 SSO 未注销）
      4. 从 claims 提取 user_id → 注入 request.state.user
    """

    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)
        self.cfg = settings.sso
        self._whitelist = self.cfg.whitelist[:]

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self.cfg.enable:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(w) for w in self._whitelist):
            return await call_next(request)

        # Read JWT from cookie or Authorization header
        jwt_str = request.cookies.get(SESSION_COOKIE, "")
        if not jwt_str:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                jwt_str = auth_header[7:]

        if not jwt_str:
            return self._unauthorized(request)

        # Verify JWT signature (HS512)
        claims = _verify_jwt(jwt_str)
        if claims is None:
            return self._unauthorized(request)

        # Optional: check SSO Redis for token validity
        user_key = claims.get("user_key", "")
        if user_key and not _check_token_redis(user_key):
            logger.info("Token revoked in SSO Redis for user_key=%s", user_key)
            return self._unauthorized(request)

        user_id = str(claims.get("user_id", ""))
        username = str(claims.get("username", ""))
        department = str(claims.get("dept_id", ""))
        roles_raw = claims.get("roles", [])
        if isinstance(roles_raw, list):
            roles = [str(r) for r in roles_raw]
        else:
            roles = [str(roles_raw)]

        from super_agent.knowledge.models import UserContext

        doc_level = "L3" if "admin" in roles else "L2"

        request.state.user = UserContext(
            user_id=user_id,
            department=department,
            tenant_id="",
            roles=roles,
            doc_level=doc_level,
        )
        # Also pass username for display purposes
        request.state.username = username

        return await call_next(request)

    def _unauthorized(self, request: Request) -> Response:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse(url="/auth/login")
        return JSONResponse(status_code=401, content={"code": 401, "message": "未登录"})


# ── SSO Router ──────────────────────


def register_sso_routes(app: FastAPI) -> None:
    """Register SSO endpoints on the FastAPI app."""

    @app.get("/auth/login")
    async def auth_login():
        authorize_url = build_authorize_url()
        return RedirectResponse(url=authorize_url)

    @app.get("/auth/callback")
    async def auth_callback(code: str = ""):
        """Handle OAuth2 callback: exchange code → store JWT in cookie."""
        if not code:
            return JSONResponse(status_code=400, content={"code": 400, "message": "缺少授权码"})

        token_data = exchange_code(code)
        if not token_data:
            return JSONResponse(status_code=502, content={"code": 502, "message": "换取 token 失败"})

        access_token = token_data.get("access_token", "")
        if not access_token:
            return JSONResponse(status_code=502, content={"code": 502, "message": "access_token 为空"})

        # Debug: inspect JWT header and our secret
        try:
            header = pyjwt.get_unverified_header(access_token)
            logger.debug("JWT header: %s", header)
        except Exception as e:
            logger.warning("Failed to parse JWT header: %s", e)
        logger.debug("Using jwt_secret (len=%d): %s...", len(settings.sso.jwt_secret), settings.sso.jwt_secret[:8])

        # Verify the JWT is valid before storing it
        claims = _verify_jwt(access_token)
        if claims is None:
            return JSONResponse(status_code=502, content={"code": 502, "message": "SSO 返回的 JWT 验证失败"})

        # Set cookie with the raw SSO JWT and redirect to frontend
        frontend_url = settings.sso.frontend_url.rstrip("/")
        response = RedirectResponse(url=frontend_url)
        response.set_cookie(
            key=SESSION_COOKIE,
            value=access_token,
            max_age=settings.sso.session_max_age,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get("/auth/logout")
    async def auth_logout():
        frontend_url = settings.sso.frontend_url.rstrip("/")
        response = RedirectResponse(url=frontend_url)
        response.delete_cookie(key=SESSION_COOKIE, httponly=True, samesite="lax")
        return response

    @app.get("/auth/me")
    async def auth_me(request: Request):
        """Return current user info from JWT session."""
        jwt_str = request.cookies.get(SESSION_COOKIE, "")
        if not jwt_str:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                jwt_str = auth_header[7:]

        claims = _verify_jwt(jwt_str)
        if claims is None:
            return JSONResponse(status_code=401, content={"code": 401, "message": "未登录或会话已过期"})

        roles_raw = claims.get("roles", [])
        if isinstance(roles_raw, list):
            roles = [str(r) for r in roles_raw]
        else:
            roles = [str(roles_raw)]

        return {
            "user_id": str(claims.get("user_id", "")),
            "username": str(claims.get("username", "")),
            "display_name": str(claims.get("username", "")),
            "roles": roles,
            "department": str(claims.get("dept_id", "")),
            "doc_level": "L3" if "admin" in roles else "L2",
        }
