import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import api_server
from public_url_security import PublicURL
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
            orig_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                res = asyncio.run(agent.run())
                self.assertFalse(res)
                self.assertEqual(agent.run_record["status"], "error")
                self.assertIn("Lost WebSocket connection", agent.run_record["error"])
                self.assertEqual(agent.run_record["test_outcome"], "unhandled_exception")

                history_path = Path("results/agent_history.json")
                self.assertTrue(history_path.exists())
                records = json.loads(history_path.read_text(encoding="utf-8"))
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["status"], "error")
                self.assertEqual(records[0]["test_outcome"], "unhandled_exception")
                self.assertEqual(records[0]["run_id"], agent.run_record["run_id"])
                self.assertIn("Lost WebSocket connection", records[0]["error"])
            finally:
                os.chdir(orig_cwd)

    def test_run_agent_sets_global_state_started_at(self):
        from datetime import datetime, timezone
        token = "f" * 64
        req = api_server.RunRequest(
            url="https://browser-agent-web-nine.vercel.app/test-quiz",
            first_name="Test",
            last_name="User",
            group_name="Group-1",
        )

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdout.readline = AsyncMock(return_value=b"")
        mock_proc.wait = AsyncMock(return_value=0)

        api_server.state_started_at = None

        validated_target = PublicURL(
            original=str(req.url),
            scheme="https",
            hostname="browser-agent-web-nine.vercel.app",
            port=443,
            path_and_query="/test-quiz",
        )
        with (
            patch.object(api_server, "API_TOKEN", "test-token"),
            patch.object(api_server, "record_run_start"),
            patch.object(
                api_server,
                "validate_public_web_url",
                AsyncMock(return_value=(validated_target, ["93.184.216.34"])),
            ),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
        ):
            res = asyncio.run(api_server.run_agent(req, "test-token", token))
            self.assertEqual(res["status"], "running")
            self.assertIsNotNone(api_server.state_started_at)
            self.assertGreater(api_server.state_started_at, 0)

            # Prove that state_started_at is used by reconcile_stale_record for local run
            local_record = {
                "run_id": res["run_id"],
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            # Active local run within timeout is NOT stale
            self.assertFalse(api_server.reconcile_stale_record(local_record))

            # If state_started_at is expired (> AGENT_RUN_TIMEOUT_SECONDS):
            api_server.state_started_at = (
                datetime.now(timezone.utc).timestamp() - (api_server.AGENT_RUN_TIMEOUT_SECONDS + 10)
            )
            # Active local run that exceeded timeout IS reconciled to abandoned
            self.assertTrue(api_server.reconcile_stale_record(local_record))
            self.assertEqual(local_record["status"], "abandoned")

    def test_reconcile_stale_record_keeps_active_remote_run(self):
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        record = {
            "run_id": "remote-active-run",
            "status": "running",
            "started_at": now_iso,
            "last_heartbeat_at": now_iso,
        }

        # Simulate a different instance where local process is None
        with patch.object(api_server, "process", None), patch.object(api_server, "state_run_key", None):
            modified = api_server.reconcile_stale_record(record)
            self.assertFalse(modified)
            self.assertEqual(record["status"], "running")

    def test_reconcile_stale_record_abandons_remote_run_after_heartbeat_timeout(self):
        from datetime import datetime, timedelta, timezone
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(timespec="seconds")

        record = {
            "run_id": "remote-dead-run",
            "status": "running",
            "started_at": old_time,
            "last_heartbeat_at": old_time,
        }

        with patch.object(api_server, "process", None), patch.object(api_server, "state_run_key", None):
            modified = api_server.reconcile_stale_record(record)
            self.assertTrue(modified)
            self.assertEqual(record["status"], "abandoned")
            self.assertEqual(record["test_outcome"], "stale_run_abandoned")

    def test_get_status_from_another_instance_reads_remote_active_run(self):
        from datetime import datetime, timezone
        token = "3" * 64
        device_id = api_server.device_owner_id(token)
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        remote_run = {
            "run_id": "remote-run-123",
            "device_id": device_id,
            "status": "running",
            "started_at": now_iso,
            "last_heartbeat_at": now_iso,
            "logs": [{"message": "Navigating to quiz page..."}],
        }

        mock_doc = MagicMock()
        mock_doc.to_dict.return_value = remote_run

        mock_query = MagicMock()
        mock_query.stream.return_value = [mock_doc]

        mock_runs_col = MagicMock()
        mock_runs_col.order_by.return_value.limit.return_value = mock_query

        mock_device_doc = MagicMock()
        mock_device_doc.collection.return_value = mock_runs_col

        mock_devices_col = MagicMock()
        mock_devices_col.document.return_value = mock_device_doc

        mock_client = MagicMock()
        mock_client.collection.return_value = mock_devices_col

        with (
            patch.object(api_server, "API_TOKEN", "test-token"),
            patch.object(api_server, "state_device_id", None),
            patch.object(api_server, "process", None),
            patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch("google.cloud.firestore.Client", return_value=mock_client),
        ):
            status_data = asyncio.run(api_server.get_status("test-token", token))
            self.assertEqual(status_data["status"], "running")
            self.assertEqual(status_data["logs"], ["Navigating to quiz page..."])

    def test_get_status_from_another_instance_reads_recently_completed_run(self):
        from datetime import datetime, timezone
        token = "4" * 64
        device_id = api_server.device_owner_id(token)
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        completed_run = {
            "run_id": "remote-run-done",
            "device_id": device_id,
            "status": "completed",
            "started_at": now_iso,
            "finished_at": now_iso,
            "logs": [{"message": "Process exited with code 0"}],
        }

        mock_doc = MagicMock()
        mock_doc.to_dict.return_value = completed_run

        mock_query = MagicMock()
        mock_query.stream.return_value = [mock_doc]

        mock_runs_col = MagicMock()
        mock_runs_col.order_by.return_value.limit.return_value = mock_query

        mock_device_doc = MagicMock()
        mock_device_doc.collection.return_value = mock_runs_col

        mock_devices_col = MagicMock()
        mock_devices_col.document.return_value = mock_device_doc

        mock_client = MagicMock()
        mock_client.collection.return_value = mock_devices_col

        with (
            patch.object(api_server, "API_TOKEN", "test-token"),
            patch.object(api_server, "state_device_id", None),
            patch.object(api_server, "process", None),
            patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch("google.cloud.firestore.Client", return_value=mock_client),
        ):
            status_data = asyncio.run(api_server.get_status("test-token", token))
            self.assertEqual(status_data["status"], "completed")
            self.assertEqual(status_data["logs"], ["Process exited with code 0"])


if __name__ == "__main__":
    unittest.main()
