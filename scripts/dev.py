"""Run and own the two local web processes, with a named fault scenario."""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "healthy": (False, False, False),
    "n-plus-one": (True, False, False),
    "missing-index": (False, True, False),
    "redundant-shipping": (False, False, True),
    "all": (True, True, True),
}


def available(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def wait_for_service(url: str, process: subprocess.Popen) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"A service exited; inspect {ROOT / 'var' / 'logs'}.")
        try:
            with urllib.request.urlopen(url, timeout=1) as result:
                if result.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {url}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="healthy")
    parser.add_argument("--jaeger", action="store_true", help="Also export traces to local Jaeger")
    parser.add_argument("--stop", action="store_true", help="Stop only the recorded local launcher")
    args = parser.parse_args()
    os.chdir(ROOT)
    state_path = ROOT / "var" / "local-processes.json"
    if args.stop:
        if not state_path.exists():
            print("No recorded local launcher is running.")
            return
        state = json.loads(state_path.read_text(encoding="utf-8"))
        # Ask the launcher to clean up its own children; do not kill arbitrary port users.
        Path(state["stop_file"]).write_text("stop", encoding="utf-8")
        for _ in range(100):
            if not state_path.exists():
                print("Celerity API and shipping service stopped. PostgreSQL remains available.")
                return
            time.sleep(0.1)
        raise SystemExit("Launcher did not stop; inspect var/logs and the recorded PIDs.")
    for port in (8000, 8001):
        if not available(port):
            raise SystemExit(
                f"Port {port} is in use. Stop the existing lab before changing scenarios."
            )
    flags = SCENARIOS[args.scenario]
    environment = os.environ.copy()
    if args.jaeger:
        environment["CELERITY_OTLP_ENDPOINT"] = "http://127.0.0.1:4318/v1/traces"
    for name, value in zip(
        ("N_PLUS_ONE", "MISSING_INDEX", "REDUNDANT_SHIPPING"), flags, strict=True
    ):
        environment[f"CELERITY_ENABLE_{name}"] = str(value).lower()
    subprocess.run([sys.executable, "-m", "celerity.cli", "init-db"], env=environment, check=True)
    logs = ROOT / "var" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stop_file = ROOT / "var" / "stop-local"
    stop_file.unlink(missing_ok=True)
    children: list[subprocess.Popen] = []
    child_stops: list[Path] = []
    handles = []
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def request_stop(*_):
        stop_file.write_text("stop", encoding="utf-8")

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        for name, port in (
            ("shipping", 8001),
            ("api", 8000),
        ):
            child_stop = ROOT / "var" / f"stop-{name}"
            child_stop.unlink(missing_ok=True)
            child_stops.append(child_stop)
            handle = (logs / f"{name}.log").open("w", encoding="utf-8")
            handles.append(handle)
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celerity.serve",
                    "--service",
                    name,
                    "--port",
                    str(port),
                    "--stop-file",
                    str(child_stop),
                ],
                cwd=ROOT,
                env=environment,
                stdout=handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            children.append(child)
            wait_for_service(f"http://127.0.0.1:{port}/health", child)
        state_path.write_text(
            json.dumps(
                {
                    "launcher": os.getpid(),
                    "children": [p.pid for p in children],
                    "scenario": args.scenario,
                    "stop_file": str(stop_file),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Celerity is running: http://127.0.0.1:8000/docs ({args.scenario})", flush=True)
        print("Press Ctrl+C, or run scripts/dev.py --stop, to stop the web services.", flush=True)
        while not stop_file.exists():
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A service stopped unexpectedly; inspect var/logs.")
            time.sleep(0.2)
    finally:
        for child_stop in child_stops:
            child_stop.write_text("stop", encoding="utf-8")
        for child in reversed(children):
            if child.poll() is None:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for child_stop in child_stops:
            child_stop.unlink(missing_ok=True)
        for handle in handles:
            handle.close()
        state_path.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
