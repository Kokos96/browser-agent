import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import api_server


class DeviceHistoryTests(unittest.TestCase):
    def test_device_token_maps_to_stable_opaque_owner_id(self):
        token = "a" * 64
        owner_id = api_server.device_owner_id(token)

        self.assertEqual(owner_id, hashlib.sha256(token.encode("ascii")).hexdigest())
        self.assertEqual(api_server.device_owner_id(token), owner_id)
        self.assertNotEqual(owner_id, api_server.device_owner_id("b" * 64))

    def test_model_allowlist_contains_default_and_current_flash_models(self):
        self.assertIn("gemini-4-argon", api_server.SUPPORTED_GEMINI_MODELS)
        self.assertIn("gemini-3.8-flash", api_server.SUPPORTED_GEMINI_MODELS)
        self.assertIn("gemini-3.6-flash", api_server.SUPPORTED_GEMINI_MODELS)
        self.assertIn("gemini-3.5-flash-lite", api_server.SUPPORTED_GEMINI_MODELS)
        self.assertNotIn("untrusted-model-name", api_server.SUPPORTED_GEMINI_MODELS)

    def test_invalid_device_tokens_are_rejected(self):
        for token in (None, "", "abc", "g" * 64, "A" * 64):
            with self.subTest(token=token):
                with self.assertRaises(HTTPException):
                    api_server.device_owner_id(token)

    def test_history_endpoint_returns_only_the_calling_devices_records(self):
        token_a = "a" * 64
        token_b = "b" * 64
        device_a = api_server.device_owner_id(token_a)
        device_b = api_server.device_owner_id(token_b)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "results"
            results.mkdir()
            history_file = results / "agent_history.json"
            history_file.write_text(
                json.dumps([
                    {"device_id": device_a, "run_id": "a-run", "started_at": "2026-10-22T10:00:00"},
                    {"device_id": device_b, "run_id": "b-run", "started_at": "2026-10-22T11:00:00"},
                    {"run_id": "legacy-run", "started_at": "2026-10-22T12:00:00"},
                ]),
                encoding="utf-8",
            )

            async def read_history():
                with (
                    patch.object(api_server, "API_TOKEN", "test-api-token"),
                    patch.object(api_server, "__file__", str(root / "api_server.py")),
                    patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GCP_PROJECT": ""}),
                ):
                    return await api_server.get_history("test-api-token", token_a)

            result = asyncio.run(read_history())
            self.assertEqual([record["run_id"] for record in result["history"]], ["a-run"])

    def test_status_endpoint_hides_other_devices_logs(self):
        token_a = "a" * 64
        token_b = "b" * 64
        device_a = api_server.device_owner_id(token_a)

        async def read_status():
            with (
                patch.object(api_server, "API_TOKEN", "test-api-token"),
                patch.object(api_server, "state_device_id", device_a),
                patch.object(api_server, "state", "running"),
                patch.object(api_server, "logs", ["private log"]),
            ):
                return await api_server.get_status("test-api-token", token_b)

        result = asyncio.run(read_status())
        self.assertEqual(result, {"status": "idle", "logs": []})

    def test_missing_history_file_is_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "results").mkdir()
            with patch.object(api_server, "__file__", str(root / "api_server.py")):
                self.assertEqual(api_server.remove_local_history_records("device-a"), 0)

    def test_deletion_only_removes_matching_device_and_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "results"
            results.mkdir()
            history_file = results / "agent_history.json"
            history_file.write_text(
                json.dumps([
                    {"device_id": "device-a", "run_id": "run-1"},
                    {"device_id": "device-a", "run_id": "run-2"},
                    {"device_id": "device-b", "run_id": "run-3"},
                    {"run_id": "legacy"},
                ]),
                encoding="utf-8",
            )

            with patch.object(api_server, "__file__", str(root / "api_server.py")):
                deleted = api_server.remove_local_history_records("device-a", "run-1")
                self.assertEqual(deleted, 1)

                remaining = json.loads(history_file.read_text(encoding="utf-8"))
                self.assertEqual(
                    [(item.get("device_id"), item.get("run_id")) for item in remaining],
                    [
                        ("device-a", "run-2"),
                        ("device-b", "run-3"),
                        (None, "legacy"),
                    ],
                )

                deleted = api_server.remove_local_history_records("device-a")
                self.assertEqual(deleted, 1)
                remaining = json.loads(history_file.read_text(encoding="utf-8"))
                self.assertEqual(
                    [(item.get("device_id"), item.get("run_id")) for item in remaining],
                    [("device-b", "run-3"), (None, "legacy")],
                )


if __name__ == "__main__":
    unittest.main()
