import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import api_server
from browser_agent.agent import BrowserAgent


class RunTimeoutAndLifecycleTests(unittest.TestCase):
    def test_record_run_start_creates_initial_running_record(self):
        token = "a" * 64
        device_id = api_server.device_owner_id(token)
        run_id = "test-run-initial"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(api_server, "__file__", str(root / "api_server.py")),
                patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GCP_PROJECT": ""}),
            ):
                api_server.record_run_start(device_id, run_id, "https://example.com/quiz", "gemini-3.6-flash")

                history_file = root / "results" / "agent_history.json"
                self.assertTrue(history_file.exists())
                records = json.loads(history_file.read_text(encoding="utf-8"))
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["device_id"], device_id)
                self.assertEqual(records[0]["run_id"], run_id)
                self.assertEqual(records[0]["status"], "running")
                self.assertEqual(records[0]["test_outcome"], "pending")
                self.assertEqual(records[0]["url"], "https://example.com/quiz")
                self.assertIsNotNone(records[0]["started_at"])

    def test_collect_output_handles_timeout_kills_process_and_marks_status(self):
        token = "b" * 64
        device_id = api_server.device_owner_id(token)
        run_id = "test-timeout-run"

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.kill = MagicMock()

        # Mock stdout readline that hangs forever
        async def hanging_readline():
            await asyncio.sleep(10)
            return b""

        mock_proc.stdout.readline = hanging_readline

        async def mock_wait():
            mock_proc.returncode = 124
            return 124

        mock_proc.wait = mock_wait

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(api_server, "__file__", str(root / "api_server.py")),
                patch.object(api_server, "AGENT_RUN_TIMEOUT_SECONDS", 0.05),
                patch.object(api_server, "state_run_key", run_id),
                patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GCP_PROJECT": ""}),
            ):
                asyncio.run(api_server.collect_output(mock_proc, run_id, device_id, run_id))

                self.assertTrue(mock_proc.kill.called)
                self.assertEqual(api_server.state, "timeout")

                history_file = root / "results" / "agent_history.json"
                records = json.loads(history_file.read_text(encoding="utf-8"))
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["status"], "timeout")
                self.assertEqual(records[0]["process_exit_code"], 124)
                self.assertEqual(records[0]["test_outcome"], "timeout")
                self.assertIn("timed out after 0.05s", records[0]["logs"][-1]["message"])

    def test_collect_output_handles_reader_exception_and_marks_error(self):
        token = "c" * 64
        device_id = api_server.device_owner_id(token)
        run_id = "test-reader-error"

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.kill = MagicMock()

        async def broken_readline():
            raise RuntimeError("Corrupted stdout pipe")

        mock_proc.stdout.readline = broken_readline

        async def mock_wait():
            mock_proc.returncode = 1
            return 1

        mock_proc.wait = mock_wait

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(api_server, "__file__", str(root / "api_server.py")),
                patch.object(api_server, "state_run_key", run_id),
                patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GCP_PROJECT": ""}),
            ):
                asyncio.run(api_server.collect_output(mock_proc, run_id, device_id, run_id))

                self.assertTrue(mock_proc.kill.called)
                self.assertEqual(api_server.state, "error")

                history_file = root / "results" / "agent_history.json"
                records = json.loads(history_file.read_text(encoding="utf-8"))
                self.assertEqual(records[0]["status"], "error")
                self.assertIn("Corrupted stdout pipe", records[0]["logs"][0]["message"])

    def test_stale_running_record_reconciled_by_get_history(self):
        token = "d" * 64
        device_id = api_server.device_owner_id(token)
        stale_run_id = "5471ec26dcec4a77ac92e9b976ce42fb"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = root / "results"
            results.mkdir()
            history_file = results / "agent_history.json"
            history_file.write_text(
                json.dumps([
                    {
                        "device_id": device_id,
                        "run_id": stale_run_id,
                        "status": "running",
                        "test_outcome": "pending",
                        "started_at": "2026-10-25T19:00:00+00:00",
                        "url": "https://browser-agent-web-nine.vercel.app/test-quiz",
                    }
                ]),
                encoding="utf-8",
            )

            with (
                patch.object(api_server, "API_TOKEN", "test-token"),
                patch.object(api_server, "__file__", str(root / "api_server.py")),
                patch.object(api_server, "process", None),
                patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "", "GCP_PROJECT": ""}),
            ):
                result = asyncio.run(api_server.get_history("test-token", token))

                self.assertEqual(len(result["history"]), 1)
                item = result["history"][0]
                self.assertEqual(item["run_id"], stale_run_id)
                self.assertEqual(item["status"], "abandoned")
                self.assertEqual(item["test_outcome"], "stale_run_abandoned")
                self.assertIn("stale run", item["error"])

                # Verify file was updated in-place with reconciled status
                saved_records = json.loads(history_file.read_text(encoding="utf-8"))
                self.assertEqual(saved_records[0]["status"], "abandoned")
                self.assertEqual(saved_records[0]["test_outcome"], "stale_run_abandoned")

    def test_get_status_reconciles_finished_process(self):
        token = "e" * 64
        device_id = api_server.device_owner_id(token)

        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with (
            patch.object(api_server, "API_TOKEN", "test-token"),
            patch.object(api_server, "state_device_id", device_id),
            patch.object(api_server, "state", "running"),
            patch.object(api_server, "process", mock_proc),
        ):
            status_data = asyncio.run(api_server.get_status("test-token", token))
            self.assertEqual(status_data["status"], "completed")

    def test_browser_agent_fatal_exception_preserves_error_history(self):
        mock_browser = MagicMock()
        mock_llm = MagicMock()

        agent = BrowserAgent(mock_browser, mock_llm)

        async def raise_fatal():
            raise ConnectionResetError("Lost WebSocket connection to Chromium")

        agent._run_internal = raise_fatal

        with tempfile.TemporaryDirectory() as tmp:
            with patch("os.makedirs"), patch("builtins.open", unittest.mock.mock_open()):
                res = asyncio.run(agent.run())
                self.assertFalse(res)
                self.assertEqual(agent.run_record["status"], "error")
                self.assertIn("Lost WebSocket connection", agent.run_record["error"])
                self.assertEqual(agent.run_record["test_outcome"], "unhandled_exception")


if __name__ == "__main__":
    unittest.main()
