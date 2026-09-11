# -*- coding: utf-8 -*-
"""账号列表添加 ChatGPT 密码后台队列。"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from config import password as _cfg
from core import db
from core.password_service import add_password, account_password
from core.session import BrowserSession

logger = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="password")
_RUNNING: set[int] = set()
_LOCK = threading.Lock()
_SLOTS = threading.BoundedSemaphore(50)


def _normalize_proxy(proxy: str | None) -> str | None:
    text = str(proxy or "").strip()
    if text.lower().startswith(("http://", "https://", "socks5://", "socks5h://", "socks4://", "socks4a://")):
        return text
    return None


def _run(account_id: int, email: str, proxy: str | None, trigger: str) -> dict:
    try:
        with _LOCK:
            _RUNNING.add(int(account_id))
        if not db.mark_account_password_setup_running(account_id):
            return {"ok": False, "status": "failed", "error": "账号已删除、已有密码或任务已被重置"}
        attempts = max(1, min(5, int(getattr(_cfg, "PASSWORD_SETUP_RETRIES", 2) or 2)))
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                session = BrowserSession(proxy=_normalize_proxy(proxy), fingerprint_seed=f"account:{email.lower()}:password")
                result = add_password(session, email)
                payload = {"ok": True, "status": "success", "password": result.get("password"), "session": result.get("session"), "message": "密码设置完成"}
                db.update_account_password(account_id, result=payload)
                try:
                    from core.remote_import import push_account
                    remote = push_account(db.get_account(account_id) or {}, force=True)
                    db.update_account_remote_import(account_id, remote)
                except Exception as exc:
                    logger.warning("[密码] Space 同步失败，不影响密码设置: %s", str(exc)[:180])
                return payload
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                logger.warning("[密码] 设置失败 %s attempt=%s/%s: %s", email, attempt, attempts, last_error)
        result = {"ok": False, "status": "failed", "error": last_error or "密码设置失败", "message": "密码设置失败"}
        db.update_account_password(account_id, result=result)
        return result
    finally:
        with _LOCK:
            _RUNNING.discard(int(account_id))
        _SLOTS.release()


def is_running(account_id: int) -> bool:
    with _LOCK:
        return int(account_id) in _RUNNING


def enqueue(account_id: int, email: str, proxy: str | None = None, trigger: str = "manual") -> dict:
    account_id = int(account_id)
    email = str(email or "").strip()
    if not email:
        return {"accepted": False, "busy": False, "error": "邮箱为空"}
    if not _SLOTS.acquire(blocking=False):
        return {"accepted": False, "busy": False, "error": "密码设置队列已满"}
    if not db.claim_account_password_setup(account_id, trigger=trigger):
        _SLOTS.release()
        row = db.get_account(account_id)
        if row and account_password(row):
            return {"accepted": False, "busy": False, "skipped": True, "error": "该账号已有 ChatGPT 密码"}
        return {"accepted": False, "busy": True, "error": "该账号正在设置密码或不存在"}
    try:
        future = _EXECUTOR.submit(_run, account_id, email, proxy, trigger)
        return {"accepted": True, "busy": False, "future": future}
    except Exception as exc:
        _SLOTS.release()
        db.update_account_password(account_id, result={"ok": False, "status": "failed", "error": str(exc)})
        return {"accepted": False, "busy": False, "error": f"{type(exc).__name__}: {exc}"}
