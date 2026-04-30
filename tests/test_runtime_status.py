from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from token_meme_monitor.runtime_status import RuntimeService, build_runtime_status


class RuntimeStatusTests(unittest.TestCase):
    def test_running_service_reports_command_log_and_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            pid_file = runtime_dir / "worker.pid"
            log_file = runtime_dir / "worker.log"
            pid_file.write_text("123\n", encoding="utf-8")
            log_file.write_text("started\n", encoding="utf-8")

            report = build_runtime_status(
                [
                    RuntimeService(
                        name="worker",
                        pid_file=pid_file,
                        log_file=log_file,
                        expected_command="token_meme_monitor run-worker",
                    )
                ],
                pid_is_running=lambda pid: pid == 123,
                command_for_pid=lambda pid: "/tmp/python -m token_meme_monitor run-worker",
            )

            service = report["services"][0]
            self.assertEqual(service["name"], "worker")
            self.assertEqual(service["state"], "running")
            self.assertEqual(service["pid"], 123)
            self.assertEqual(service["log_size_bytes"], len("started\n"))
            self.assertEqual(service["diagnostics"], [])

    def test_stale_pid_file_is_reported_as_stopped_with_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            pid_file = runtime_dir / "worker.pid"
            log_file = runtime_dir / "worker.log"
            pid_file.write_text("123\n", encoding="utf-8")

            report = build_runtime_status(
                [
                    RuntimeService(
                        name="worker",
                        pid_file=pid_file,
                        log_file=log_file,
                        expected_command="token_meme_monitor run-worker",
                    )
                ],
                pid_is_running=lambda pid: False,
                command_for_pid=lambda pid: "",
            )

            service = report["services"][0]
            self.assertEqual(service["state"], "stopped")
            self.assertIn("stale_pid", service["diagnostics"])

    def test_command_mismatch_is_not_marked_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = Path(tmpdir)
            pid_file = runtime_dir / "worker.pid"
            log_file = runtime_dir / "worker.log"
            pid_file.write_text("123\n", encoding="utf-8")

            report = build_runtime_status(
                [
                    RuntimeService(
                        name="worker",
                        pid_file=pid_file,
                        log_file=log_file,
                        expected_command="token_meme_monitor run-worker",
                    )
                ],
                pid_is_running=lambda pid: True,
                command_for_pid=lambda pid: "/usr/bin/python unrelated.py",
            )

            service = report["services"][0]
            self.assertEqual(service["state"], "mismatch")
            self.assertIn("command_mismatch", service["diagnostics"])


if __name__ == "__main__":
    unittest.main()
