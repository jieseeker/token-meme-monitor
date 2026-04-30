from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from token_meme_monitor.utils import isoformat_utc, utcnow


DEFAULT_RUNTIME_DIR = "/tmp/token-meme-monitor"


@dataclass(frozen=True)
class RuntimeService:
    name: str
    pid_file: Path
    log_file: Path
    expected_command: str
    endpoint: str | None = None


def default_runtime_services(
    runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
    *,
    dashboard_host: str = "127.0.0.1",
    dashboard_port: int = 8501,
) -> list[RuntimeService]:
    root = Path(runtime_dir)
    pid_dir = root / "pids"
    log_dir = root / "logs"
    return [
        RuntimeService(
            name="worker",
            pid_file=pid_dir / "worker.pid",
            log_file=log_dir / "worker.log",
            expected_command="token_meme_monitor run-worker",
        ),
        RuntimeService(
            name="scheduled-backtest",
            pid_file=pid_dir / "scheduled-backtest.pid",
            log_file=log_dir / "scheduled-backtest.log",
            expected_command="token_meme_monitor run-scheduled-backtest-worker",
        ),
        RuntimeService(
            name="dashboard",
            pid_file=pid_dir / "dashboard.pid",
            log_file=log_dir / "dashboard.log",
            expected_command="streamlit run dashboard/app.py",
            endpoint=f"http://{dashboard_host}:{dashboard_port}",
        ),
    ]


def build_runtime_status(
    services: list[RuntimeService] | None = None,
    *,
    pid_is_running: Callable[[int], bool] | None = None,
    command_for_pid: Callable[[int], str] | None = None,
) -> dict[str, Any]:
    pid_checker = pid_is_running or is_pid_running
    command_lookup = command_for_pid or get_process_command
    service_statuses = [_service_status(service, pid_checker, command_lookup) for service in services or default_runtime_services()]
    running = sum(1 for service in service_statuses if service["state"] == "running")
    unhealthy = sum(1 for service in service_statuses if service["state"] == "mismatch")
    return {
        "generated_at": isoformat_utc(utcnow()),
        "summary": {
            "total": len(service_statuses),
            "running": running,
            "stopped": sum(1 for service in service_statuses if service["state"] == "stopped"),
            "unhealthy": unhealthy,
        },
        "services": service_statuses,
    }


def render_runtime_status(report: dict[str, Any]) -> str:
    lines = ["Runtime Status"]
    for service in report.get("services") or []:
        parts = [
            f"- {service.get('name')}: {service.get('state')}",
            f"pid={service.get('pid') or '-'}",
            f"log={service.get('log_file')}",
            f"log_size={service.get('log_size_bytes', 0)}",
        ]
        if service.get("endpoint"):
            parts.append(f"endpoint={service.get('endpoint')}")
        diagnostics = service.get("diagnostics") or []
        if diagnostics:
            parts.append("diagnostics=" + ",".join(str(item) for item in diagnostics))
        lines.append(" ".join(parts))
    return "\n".join(lines)


def is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def get_process_command(pid: int) -> str:
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _service_status(
    service: RuntimeService,
    pid_is_running: Callable[[int], bool],
    command_for_pid: Callable[[int], str],
) -> dict[str, Any]:
    diagnostics: list[str] = []
    pid = _read_pid(service.pid_file, diagnostics)
    command = ""
    state = "stopped"

    if pid is not None:
        if pid_is_running(pid):
            command = command_for_pid(pid)
            if command and service.expected_command not in command:
                state = "mismatch"
                diagnostics.append("command_mismatch")
            else:
                state = "running"
                if not command:
                    diagnostics.append("command_unavailable")
        else:
            diagnostics.append("stale_pid")

    log_stats = _log_stats(service.log_file)
    return {
        "name": service.name,
        "state": state,
        "pid": pid,
        "pid_file": str(service.pid_file),
        "command": command,
        "expected_command": service.expected_command,
        "log_file": str(service.log_file),
        "log_exists": log_stats["exists"],
        "log_size_bytes": log_stats["size_bytes"],
        "log_updated_at": log_stats["updated_at"],
        "endpoint": service.endpoint,
        "diagnostics": diagnostics,
    }


def _read_pid(pid_file: Path, diagnostics: list[str]) -> int | None:
    if not pid_file.exists():
        return None
    raw = pid_file.read_text(encoding="utf-8").strip()
    try:
        pid = int(raw)
    except ValueError:
        diagnostics.append("invalid_pid")
        return None
    if pid <= 0:
        diagnostics.append("invalid_pid")
        return None
    return pid


def _log_stats(log_file: Path) -> dict[str, Any]:
    if not log_file.exists():
        return {"exists": False, "size_bytes": 0, "updated_at": None}
    stat = log_file.stat()
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "updated_at": isoformat_utc(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)),
    }
