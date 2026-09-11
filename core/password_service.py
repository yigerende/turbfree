# -*- coding: utf-8 -*-
"""ChatGPT 注册后/账号列表添加密码流程。"""
from __future__ import annotations

import json
import logging
import secrets
import string
import time
from urllib.parse import urlencode

from config import password as _cfg
from core.chatgpt_auth import get_csrf_token
from core.openai_auth import follow_authorize, validate_email_otp
from core.account_export import fetch_session, follow_oauth_callback
from core.session import BrowserSession

logger = logging.getLogger(__name__)


def account_password(row: dict | None) -> str:
    row = row or {}
    extra = row.get("extra_json")
    if isinstance(extra, str) and extra.strip():
        try:
            extra = json.loads(extra)
        except Exception:
            extra = {}
    if not isinstance(extra, dict):
        extra = {}
    return str(extra.get("registration_password") or row.get("registration_password") or "").strip()


def generate_password(length: int = 16) -> str:
    """生成满足常见密码策略的随机密码。"""
    length = max(12, min(64, int(length or 16)))
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*_-"
    chars = [secrets.choice(string.ascii_lowercase), secrets.choice(string.ascii_uppercase), secrets.choice(string.digits), secrets.choice("!@#$%^&*_-" )]
    chars.extend(secrets.choice(alphabet) for _ in range(length - len(chars)))
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def configured_password() -> str:
    mode = str(getattr(_cfg, "CHATGPT_PASSWORD_MODE", "random") or "random").strip().lower()
    if mode == "fixed":
        value = str(getattr(_cfg, "CHATGPT_FIXED_PASSWORD", "") or "").strip()
        if not value:
            raise RuntimeError("已选择固定密码模式，但固定 ChatGPT 密码为空")
        return value
    return generate_password()


def _start_add_password(session: BrowserSession, email: str) -> str:
    """按完整 HAR 构造 post_login_add_password 授权请求。"""
    csrf = get_csrf_token(session)
    query = {
        "login_hint": email,
        "reauth": "password",
        "post_login_add_password": "true",
        "max_age": "0",
        "ext-oai-did": session.device_id,
    }
    url = "https://chatgpt.com/api/auth/signin/openai?" + urlencode(query)
    headers = session.get_nextauth_headers(referer="https://chatgpt.com/")
    headers.update({"content-type": "application/x-www-form-urlencoded", "origin": "https://chatgpt.com"})
    body = urlencode({"callbackUrl": "https://chatgpt.com/", "csrfToken": csrf, "json": "true"})
    response = session.post(url, headers=headers, data=body)
    response.raise_for_status()
    auth_url = response.json().get("url")
    if not auth_url:
        raise RuntimeError("添加密码授权响应缺少 url")
    return str(auth_url)


def add_password(
    session: BrowserSession,
    email: str,
    *,
    password: str | None = None,
    otp_code: str | None = None,
    after_ts: float | None = None,
) -> dict:
    """在同一认证会话中完成二次 OTP 和 /api/accounts/password/add。"""
    from config import email as email_cfg
    from core.email_provider import wait_for_otp

    email = str(email or "").strip()
    if not email:
        raise ValueError("email 为空")
    chosen = str(password or "").strip() or configured_password()
    auth_url = _start_add_password(session, email)
    sent_at = float(after_ts or time.time())
    follow_authorize(session, auth_url)
    if otp_code is None:
        if not bool(getattr(email_cfg, "USE_EMAIL_SERVICE", False)):
            raise RuntimeError("添加密码需要启用邮箱自动取码，或由调用方传入 otp_code")
        otp_code = wait_for_otp(email, after_ts=sent_at)
    validation = validate_email_otp(session, str(otp_code).strip())
    page = validation.get("page") if isinstance(validation, dict) else {}
    page_type = str((page or {}).get("type") or "") if isinstance(page, dict) else ""
    if page_type and page_type != "reset_password_new_password":
        raise RuntimeError(f"验证码通过后未进入设置密码页: page_type={page_type}")

    sentinel = None
    try:
        from core.openai_auth import request_sentinel_token, build_sentinel_header
        sentinel_resp = request_sentinel_token(session, "authorize_continue")
        sentinel, _ = build_sentinel_header(session, sentinel_resp, "authorize_continue")
    except Exception as exc:
        logger.warning("[密码] 获取 Sentinel 失败，继续尝试 password/add: %s", str(exc)[:160])

    headers = session.get_auth_headers(referer="https://auth.openai.com/reset-password/new-password")
    if sentinel:
        headers["openai-sentinel-token"] = sentinel
    response = session.post(
        "https://auth.openai.com/api/accounts/password/add",
        headers=headers,
        data=json.dumps({"password": chosen}),
    )
    if response.status_code != 200:
        raise RuntimeError(f"password/add HTTP {response.status_code}: {(response.text or '')[:240]}")

    # 已登录的注册会话通常已经有 ChatGPT session cookie；如果服务端返回继续地址，
    # 则优先跟随它，再读取最终 /api/auth/session。
    try:
        body = response.json() if response.content else {}
    except Exception:
        body = {}
    continue_url = body.get("continue_url") if isinstance(body, dict) else None
    if continue_url:
        follow_oauth_callback(session, str(continue_url), referer="https://auth.openai.com/reset-password/new-password")
    session_info = fetch_session(session)
    return {"password": chosen, "session": session_info, "validation": validation}
