# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from core import twofa_service


class TwoFARemoteSyncTests(unittest.TestCase):
    def test_sync_pushes_latest_account_and_updates_status(self):
        account = {
            "id": 7,
            "email": "twofa@example.com",
            "access_token": "at",
            "totp_secret": "JBSWY3DPEHPK3PXP",
        }
        pushed = {"ok": True, "status": "success", "message": "已推送"}
        with patch("config.remote_import.REMOTE_IMPORT_ENABLED", True), patch(
            "core.db.get_account", return_value=account
        ), patch(
            "core.remote_import.push_account", return_value=pushed
        ) as push, patch(
            "core.db.update_account_remote_import"
        ) as update, patch(
            "core.twofa_service._append_log"
        ):
            result = twofa_service._sync_remote_after_twofa(7, "twofa@example.com")

        self.assertEqual(result, pushed)
        push.assert_called_once_with(account, force=True)
        update.assert_called_once_with(7, pushed)

    def test_sync_is_skipped_when_automatic_remote_import_is_disabled(self):
        with patch("config.remote_import.REMOTE_IMPORT_ENABLED", False), patch(
            "core.db.get_account"
        ) as get_account:
            result = twofa_service._sync_remote_after_twofa(7, "twofa@example.com")

        self.assertEqual(result["status"], "skipped")
        get_account.assert_not_called()


if __name__ == "__main__":
    unittest.main()
