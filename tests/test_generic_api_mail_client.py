import unittest
from unittest.mock import Mock

from core import generic_api_mail_client as client


class GenericApiMailClientTests(unittest.TestCase):
    def test_extracts_german_chatgpt_verification_code(self):
        code = client._extract_yangyang_openai_code(
            "Dein temporärer Bestätigungscode für ChatGPT",
            "Gib diesen temporären Verifizierungscode ein: 556097",
        )
        self.assertEqual(code, "556097")

    def test_query_messages_api_supports_nested_data_and_iso_utc_time(self):
        path_response = Mock(status_code=404, text="not found")
        query_response = Mock(status_code=200)
        query_response.json.return_value = {
            "success": True,
            "data": {
                "messages": [{
                    "id": 38691,
                    "from": "ChatGPT <otp@example.com>",
                    "subject": "Dein temporärer Bestätigungscode für ChatGPT",
                    "body": "Dein temporärer Verifizierungscode: 556097",
                    "receivedAt": "2026-09-06T00:55:14.000Z",
                }],
            },
        }
        session = Mock()
        session.get.side_effect = [path_response, query_response]

        result = client._fetch_yangyang_otp(
            session,
            "https://msg.example/messages/token/test@example.com",
            {"User-Agent": "test"},
            after_ts=1788656000,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "556097")
        self.assertEqual(result[1]["msg_ts"], 1788656114.0)


if __name__ == "__main__":
    unittest.main()
