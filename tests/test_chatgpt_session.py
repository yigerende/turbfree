# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from core import db, remote_import
from core.account_export import save_account_data
from webui.app import _account_secret_value, _compact_account_for_list


class ChatGPTSessionTests(unittest.TestCase):
    @staticmethod
    def _storage_patches(root: Path) -> dict:
        return {
            "_ACCOUNTS_JSON": root / "accounts.json",
            "_OUTLOOK_JSON": root / "outlook.json",
            "_GENERIC_API_EMAIL_JSON": root / "generic.json",
            "_DOMAIN_EMAIL_JSON": root / "domain.json",
            "_JOBS_JSON": root / "jobs.json",
            "_LEGACY_ACCOUNTS_JSON": root / "legacy-accounts.json",
            "_LEGACY_OUTLOOK_JSON": root / "legacy-outlook.json",
            "_LEGACY_JOBS_JSON": root / "legacy-jobs.json",
            "_LEGACY_SQLITE": root / "legacy.db",
            "_CODEX_DIR": root / "codex",
            "_CODEX_AGENT_DIR": root / "codex-agent",
            "_LEGACY_CODEX_EXPORT_STATE": root / "codex-export.json",
            "_SQLITE_READY": False,
            "_SQLITE_READY_PATH": None,
        }

    def test_full_session_is_saved_and_only_exposed_on_secret_request(self):
        session = {
            "user": {"id": "user-1", "email": "session@example.com"},
            "account": {"id": "account-1", "planType": "free"},
            "expires": "2026-12-01T00:00:00.000Z",
            "accessToken": "session-at",
        }
        with tempfile.TemporaryDirectory() as td, patch.multiple(
            db, **self._storage_patches(Path(td))
        ):
            account_id = save_account_data(
                email="session@example.com",
                access_token="session-at",
                chatgpt_session=session,
                auto_plan_check=False,
            )
            account = db.get_account(account_id)
            compact = _compact_account_for_list(account)

            self.assertTrue(compact["has_chatgpt_session"])
            self.assertNotIn("chatgpt_session", compact)
            self.assertEqual(
                json.loads(_account_secret_value(account, "chatgpt_session")),
                session,
            )

    def test_remote_payload_contains_complete_session(self):
        session = {
            "user": {"email": "push@example.com"},
            "accessToken": "push-at",
            "expires": "2026-12-01T00:00:00.000Z",
        }
        with patch("core.db.get_account_by_email", return_value=None), patch(
            "core.remote_import._mail_credentials_for_email", return_value={}
        ):
            payload = remote_import.build_registration_payload({
                "email": "push@example.com",
                "access_token": "push-at",
                "totp_secret": "JBSWY3DPEHPK3PXP",
                "extra_json": json.dumps({"chatgpt_session": session}),
            }, include_codex=False)
        self.assertEqual(payload["chatgpt_session"], session)
        self.assertEqual(payload["totp_secret"], "JBSWY3DPEHPK3PXP")

    def test_remote_payload_backfills_totp_from_saved_account(self):
        with patch("core.db.get_account_by_email", return_value={
            "email": "push@example.com",
            "totp_secret": "BACKFILLEDTOTP",
        }), patch(
            "core.remote_import._mail_credentials_for_email", return_value={}
        ):
            payload = remote_import.build_registration_payload({
                "email": "push@example.com",
                "access_token": "push-at",
            }, include_codex=False)
        self.assertEqual(payload["totp_secret"], "BACKFILLEDTOTP")

    def test_remote_post_retries_transient_failures(self):
        response = SimpleNamespace(status_code=200, content=b"{}", ok=True)
        response.json = lambda: {}
        with patch("core.remote_import.requests.post", side_effect=[
            remote_import.requests.ConnectionError("offline"),
            SimpleNamespace(status_code=503, content=b"{}", ok=False, json=lambda: {}),
            response,
        ]) as post, patch("core.remote_import.time.sleep"):
            actual, data = remote_import._post_remote_payload("http://space.test/import", {"email": "a@b.com"})

        self.assertIs(actual, response)
        self.assertEqual(data, {})
        self.assertEqual(post.call_count, 3)

    def test_remote_post_does_not_retry_client_error(self):
        response = SimpleNamespace(status_code=400, content=b"{}", ok=False)
        response.json = lambda: {"error": "bad request"}
        with patch("core.remote_import.requests.post", return_value=response) as post:
            actual, data = remote_import._post_remote_payload("http://space.test/import", {"email": "a@b.com"})

        self.assertIs(actual, response)
        self.assertEqual(data["error"], "bad request")
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
