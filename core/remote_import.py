# -*- coding: utf-8 -*-
from __future__ import annotations
import logging
import json
import time
import requests
from urllib.parse import urlparse
from config import remote_import as cfg

logger = logging.getLogger(__name__)


def _post_remote_payload(url: str, payload: dict) -> tuple[requests.Response, dict]:
    """POST to Space with bounded retries for transient network/5xx errors."""
    auth = (
        str(getattr(cfg, "REMOTE_IMPORT_USERNAME", "admin")),
        str(getattr(cfg, "REMOTE_IMPORT_PASSWORD", "admin")),
    )
    timeout = max(5, int(getattr(cfg, "REMOTE_IMPORT_TIMEOUT", 20) or 20))
    last_exc = None
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, auth=auth, timeout=timeout)
            data = response.json() if response.content else {}
            if response.status_code >= 500 and attempt < 2:
                time.sleep(0.7 * (attempt + 1))
                continue
            return response, data if isinstance(data, dict) else {}
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.7 * (attempt + 1))
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("远程导入请求失败")

def remote_import_url(value: str | None = None) -> str:
    """允许配置 IP:端口、域名或完整接口地址。"""
    raw = str(value if value is not None else getattr(cfg, "REMOTE_IMPORT_URL", "") or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if parsed.path in ("", "/"):
        return raw + "/api/integrations/turb/register"
    if parsed.path.rstrip("/").endswith("/api"):
        return raw + "/integrations/turb/register"
    return raw


def _codex_credentials_for_email(email: str) -> dict:
    """从本地 Codex 凭证库按邮箱读取完整 AT/RT（仅在主动推送时使用）。"""
    try:
        from core import db
        email_l = str(email or "").strip().lower()
        for item in db.list_codex_accounts():
            if str(item.get("email") or "").strip().lower() != email_l:
                continue
            filename = str(item.get("filename") or "").strip()
            if not filename:
                continue
            raw, _ = db.read_codex_credential(filename)
            content = json.loads(raw)
            if isinstance(content, dict):
                return content
    except Exception as exc:
        logger.debug("[远程导入] 读取 Codex 凭证失败: %s", exc)
    return {}


def _mail_credentials_for_email(email: str) -> dict:
    """从本地邮箱池按邮箱读取邮箱基础凭证。"""
    email_l = str(email or "").strip().lower()
    if not email_l:
        return {}
    try:
        from core import db
        for getter in (db.get_outlook_by_email, db.get_generic_api_email_by_email, db.get_imap_email_by_email):
            row = getter(email_l)
            if row:
                return row
    except Exception as exc:
        logger.debug("[远程导入] 读取邮箱池凭证失败: %s", exc)
    return {}


def _chatgpt_session_from(value: dict | None) -> dict:
    """Read the complete saved ChatGPT session without exposing it in lists."""
    value = value or {}
    for key in ("chatgpt_session", "session"):
        candidate = value.get(key)
        if isinstance(candidate, dict) and candidate:
            return candidate
        if isinstance(candidate, str) and candidate.strip():
            try:
                parsed = json.loads(candidate)
            except Exception:
                parsed = None
            if isinstance(parsed, dict) and parsed:
                return parsed
    extra = value.get("extra_json")
    if isinstance(extra, str) and extra.strip():
        try:
            extra = json.loads(extra)
        except Exception:
            extra = {}
    if isinstance(extra, dict):
        candidate = extra.get("chatgpt_session") or extra.get("session")
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def build_registration_payload(result: dict, *, include_codex: bool = True) -> dict:
    """构造 Space Console 导入载荷；source AT 必填，Codex RT 有则附带。"""
    result = result or {}
    email = str(result.get("email") or "").strip()
    payload = {
        "email": email,
        "access_token": str(result.get("access_token") or "").strip(),
        "name": result.get("name") or result.get("user_name") or "",
        "user_id": result.get("user_id") or "",
        "account_id": result.get("account_id") or result.get("personal_account_id") or "",
        "plan_type": result.get("plan_type") or result.get("current_plan_type") or "",
    }
    chatgpt_session = _chatgpt_session_from(result)
    if not chatgpt_session and email:
        try:
            from core import db
            chatgpt_session = _chatgpt_session_from(db.get_account_by_email(email) or {})
        except Exception as exc:
            logger.debug("[远程导入] 读取完整 Session 失败: %s", exc)
    if chatgpt_session:
        payload["chatgpt_session"] = chatgpt_session
    # ChatGPT/OpenAI registration password is different from the mailbox
    # password. It is present only when the registration flow showed the
    # password page; otherwise it is intentionally omitted.
    chatgpt_password = str(
        result.get("gpt_password")
        or result.get("password")
        or result.get("registration_password")
        or ""
    ).strip()
    if not chatgpt_password and email:
        try:
            from core import db
            stored = db.get_account_by_email(email)
            if stored:
                extra = stored.get("extra_json")
                if isinstance(extra, str):
                    try:
                        extra = json.loads(extra)
                    except Exception:
                        extra = {}
                chatgpt_password = str((extra or {}).get("registration_password") or stored.get("registration_password") or "").strip()
        except Exception as exc:
            logger.debug("[远程导入] 读取 ChatGPT 密码失败: %s", exc)
    if chatgpt_password:
        payload["gpt_password"] = chatgpt_password
    totp_secret = str(result.get("totp_secret") or "").strip()
    if not totp_secret and email:
        try:
            from core import db
            stored = db.get_account_by_email(email)
            totp_secret = str((stored or {}).get("totp_secret") or "").strip()
        except Exception as exc:
            logger.debug("[远程导入] 读取 2FA 密钥失败: %s", exc)
    if totp_secret:
        payload["totp_secret"] = totp_secret
    if include_codex and email:
        codex = _codex_credentials_for_email(email)
        rt = str(result.get("chatgpt_refresh_token") or result.get("oauth_refresh_token") or codex.get("refresh_token") or "").strip()
        oat = str(result.get("oauth_access_token") or codex.get("access_token") or "").strip()
        if rt:
            payload["refresh_token"] = rt
            if oat:
                payload["oauth_access_token"] = oat
            payload["oauth_account_id"] = codex.get("account_id") or codex.get("chatgpt_account_id") or ""
    mail = _mail_credentials_for_email(email)
    for out_key, keys in {
        "mail_password": ("password", "mail_password"),
        "client_id": ("client_id",),
        "mail_refresh_token": ("refresh_token", "mail_refresh_token"),
        "pickup_url": ("code_url", "pickup_url"),
    }.items():
        for key in keys:
            value = str(mail.get(key) or "").strip()
            if value:
                payload[out_key] = value
                break
    return {k: v for k, v in payload.items() if str(v or "").strip()}


def push_account(account: dict, *, force: bool = False) -> dict:
    """手动推送一个已注册账号，返回不含敏感内容的结果。"""
    if not force and not bool(getattr(cfg, "REMOTE_IMPORT_ENABLED", False)):
        return {"status": "skipped", "ok": True, "message": "未启用远程导入"}
    url = remote_import_url()
    if not url:
        return {"status": "failed", "ok": False, "message": "REMOTE_IMPORT_URL 未配置"}
    payload = build_registration_payload(account or {})
    if not payload.get("access_token"):
        return {"status": "skipped", "ok": True, "message": "账号没有 access_token"}
    try:
        response, data = _post_remote_payload(url, payload)
        if not response.ok or (isinstance(data, dict) and data.get("ok") is False):
            message = data.get("error") if isinstance(data, dict) else response.text[:200]
            raise RuntimeError(f"HTTP {response.status_code}: {message or '远程导入失败'}")
        return {"status": "success", "ok": True, "message": "已推送"}
    except Exception as exc:
        logger.warning("[远程导入] 手动推送失败：%s", exc)
        return {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {str(exc)[:180]}"}
def push_registration(result: dict) -> dict:
    if not bool(getattr(cfg, "REMOTE_IMPORT_ENABLED", False)):
        return {"status": "skipped", "ok": True, "message": "未启用远程导入"}
    url = remote_import_url()
    token = str((result or {}).get("access_token") or "").strip()
    if not url: return {"status": "failed", "ok": False, "message": "REMOTE_IMPORT_URL 未配置"}
    if not token: return {"status": "failed", "ok": False, "message": "注册结果没有 access_token"}
    enriched = {**result, "access_token": token}
    # The browser drivers persist registration_password in the account row
    # rather than returning it in the compact registration result. Load it
    # back for the automatic post-registration push when available.
    if not any(str(enriched.get(key) or "").strip() for key in ("gpt_password", "password", "registration_password")):
        try:
            from core import db
            stored = db.get_account_by_email(str(enriched.get("email") or "").strip())
            if stored:
                extra = stored.get("extra_json")
                if isinstance(extra, str):
                    try:
                        extra = json.loads(extra)
                    except Exception:
                        extra = {}
                enriched.update({"password": (extra or {}).get("registration_password") or stored.get("registration_password") or ""})
        except Exception as exc:
            logger.debug("[远程导入] 读取注册密码失败: %s", exc)
    payload = build_registration_payload(enriched)
    try:
        response, data = _post_remote_payload(url, payload)
        if not response.ok or not isinstance(data, dict) or data.get("ok") is False:
            message = data.get("error") if isinstance(data, dict) else response.text[:200]
            raise RuntimeError(f"HTTP {response.status_code}: {message or '远程导入失败'}")
        logger.info("[远程导入] 注册账号已推送到 Space Console：%s", payload["email"] or "(unknown)")
        return {"status": "success", "ok": True, "message": "已导入"}
    except Exception as exc:
        logger.warning("[远程导入] 推送失败，不影响本地注册结果：%s", exc)
        return {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {str(exc)[:180]}"}
