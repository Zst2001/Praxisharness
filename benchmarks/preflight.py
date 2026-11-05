"""Credential-free local preflight. Never invokes a model or repairs the Broker."""

from __future__ import annotations

import argparse
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

from backend.domain import safe_error_message
from backend.sandbox import FileAccessMode, SandboxLauncher, SandboxPolicy, WindowsBrokerClient

from .harness import ARMS, DEFAULT_SUITE, load_suite
from .provenance import source_provenance


def sandbox_smoke(broker: WindowsBrokerClient) -> dict:
    """Real Broker child processes, restricted to disposable test fixtures."""
    launcher = SandboxLauncher(broker=broker)
    with TemporaryDirectory(prefix="praxis-broker-smoke-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        outside = root / "outside.txt"
        outside.write_text("unchanged", encoding="utf-8")
        results = []
        for label, mode, script in (
            ("workspace_write", FileAccessMode.WORKSPACE_WRITE, "echo permitted>inside.txt"),
            ("read_only_write_denied", FileAccessMode.READ_ONLY, "echo forbidden>readonly.txt"),
            ("outside_write_denied", FileAccessMode.WORKSPACE_WRITE, 'echo forbidden>"' + str(outside) + '"'),
        ):
            process = None
            job_id = "smoke-" + uuid4().hex
            try:
                policy = SandboxPolicy(workspaces=(workspace,), session_id="preflight", job_id=job_id, file_mode=mode)
                deadline = monotonic() + 10
                launcher.wait_resources(job_id, cancelled=lambda: monotonic() >= deadline)
                process = launcher.launch(
                    [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", script], policy, cwd=workspace
                )
                process.communicate(timeout=15)
                passed = (
                    (workspace / "inside.txt").read_text(encoding="utf-8").strip() == "permitted"
                    if label == "workspace_write"
                    else not (workspace / "readonly.txt").exists()
                    if label == "read_only_write_denied"
                    else outside.read_text(encoding="utf-8") == "unchanged"
                )
                results.append(
                    {"check": label, "status": "passed" if passed else "failed", "exit_code": process.returncode}
                )
            except Exception as exc:
                results.append(
                    {
                        "check": label,
                        "status": "blocked",
                        "error_type": type(exc).__name__,
                        "code": str(getattr(exc, "code", "unknown")),
                        "detail": safe_error_message(exc),
                    }
                )
            finally:
                if process is not None:
                    if process.poll() is None:
                        launcher.terminate_tree(process)
                    if not launcher.cleanup(process):
                        results[-1].update(status="blocked", detail="Broker probe cleanup failed.")
                launcher.cancel_resource_wait(job_id)
        return {
            "status": "passed"
            if all(row["status"] == "passed" for row in results)
            else "blocked"
            if any(row["status"] == "blocked" for row in results)
            else "failed",
            "checks": results,
            "scope": "file write permissions and controller output readback; not a network or full isolation certification",
        }


def preflight(*, smoke: bool = False) -> dict:
    suite, digest = load_suite(DEFAULT_SUITE)
    selected = {}
    for task in suite["tasks"]:
        selected.setdefault(task["family"], task["id"])
    broker = WindowsBrokerClient.from_system()
    status = broker.status()
    safe_status = {
        key: value
        for key, value in status.to_dict().items()
        if key in {"installed", "healthy", "code", "version", "service_state"}
    }
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "provenance": source_provenance(),
        "environment": {"python": platform.python_version(), "platform": os.name},
        "broker": safe_status,
        "sandbox_smoke": sandbox_smoke(broker)
        if smoke and status.healthy
        else {"status": "not_run", "reason": "not_requested" if not smoke else "broker_unhealthy"},
        "real_model": {
            "status": "not_run",
            "reason": "explicit_provider_model_and_cost_limit_required",
            "requests_sent": 0,
        },
        "pilot": {
            "task_ids": list(selected.values()),
            "arms": list(ARMS),
            "repeat": 1,
            "trials": len(selected) * len(ARMS),
            "suite_sha256": digest,
        },
        "full_experiment": {
            "tasks": len(suite["tasks"]),
            "arms": len(ARMS),
            "repeat": 3,
            "trials": len(suite["tasks"]) * len(ARMS) * 3,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sandbox-smoke", action="store_true", help="Run temporary file-permission probes through the real Broker."
    )
    parser.add_argument("--output", type=Path, default=Path("benchmarks/output/preflight.json"))
    args = parser.parse_args()
    report = preflight(smoke=args.sandbox_smoke)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "broker": report["broker"],
                "smoke": report["sandbox_smoke"],
                "model_requests": 0,
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
