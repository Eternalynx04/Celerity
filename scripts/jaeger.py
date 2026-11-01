"""Project-local Jaeger v2 for Windows; verify its published binary checksum."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.22.0"
NAME = f"jaeger-{VERSION}-windows-amd64"
BASE = f"https://github.com/jaegertracing/jaeger/releases/download/v{VERSION}"
RUNTIME = ROOT / ".runtime" / "jaeger"
BINARY = RUNTIME / "jaeger.exe"
STATE = ROOT / "var" / "jaeger-process.json"


def setup() -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if BINARY.exists():
        return
    archive = RUNTIME / "download.zip"
    print(f"Downloading Jaeger {VERSION} from its official GitHub release...", flush=True)
    urllib.request.urlretrieve(f"{BASE}/{NAME}.zip", archive)
    checksum_text = (
        urllib.request.urlopen(f"{BASE}/{NAME}.sha256sum.txt", timeout=30).read().decode()
    )
    with zipfile.ZipFile(archive) as bundle:
        member = next(item for item in bundle.namelist() if item.endswith("/jaeger.exe"))
        content = bundle.read(member)
    expected = next(
        line.split()[0]
        for line in checksum_text.splitlines()
        if line.split()[-1].lstrip("*").endswith("/jaeger.exe")
    )
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise RuntimeError("Jaeger binary checksum does not match its published checksum.")
    BINARY.write_bytes(content)
    (RUNTIME / "source.json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "source": BASE,
                "binary_sha256": actual,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    archive.unlink()


def status() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:13133/status", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "start", "stop", "status"])
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Use Docker Compose's observability profile on non-Windows systems.")
    if args.action == "status":
        print("Jaeger is ready." if status() else "Jaeger is not ready.")
        return
    if args.action == "stop":
        if not STATE.exists():
            print("No project-owned Jaeger process is recorded.")
            return
        pid = json.loads(STATE.read_text(encoding="utf-8"))["pid"]
        try:
            process = psutil.Process(pid)
            if Path(process.exe()).resolve() != BINARY.resolve():
                raise RuntimeError("PID belongs to another executable; refusing to stop it.")
            process.terminate()
            process.wait(timeout=10)
        except psutil.NoSuchProcess:
            pass
        STATE.unlink(missing_ok=True)
        print("Jaeger stopped; its in-memory trace view has been cleared.")
        return
    setup()
    if args.action == "setup":
        return
    if STATE.exists() and status():
        print("Project Jaeger is already ready: http://127.0.0.1:16686")
        return
    for port in (16686, 16685, 4318, 4317, 13133):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is occupied; no existing process was stopped.")
    logs = ROOT / "var" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "jaeger.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(
            [str(BINARY), "--config", str(ROOT / "observability/jaeger.yaml")],
            cwd=ROOT,
            env=os.environ | {"JAEGER_LISTEN_HOST": "127.0.0.1"},
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    for _ in range(100):
        if child.poll() is not None:
            raise RuntimeError("Jaeger exited; inspect var/logs/jaeger.log.")
        if status():
            STATE.write_text(json.dumps({"pid": child.pid, "version": VERSION}), encoding="utf-8")
            print("Jaeger ready: http://127.0.0.1:16686", flush=True)
            return
        time.sleep(0.1)
    child.terminate()
    child.wait()
    raise RuntimeError("Jaeger did not become ready.")


if __name__ == "__main__":
    main()
