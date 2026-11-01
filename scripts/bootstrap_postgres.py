"""Install project-local Windows PostgreSQL binaries; no service or global PATH edits."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
VERSION = "17.11-1"
URL = f"https://get.enterprisedb.com/postgresql/postgresql-{VERSION}-windows-x64-binaries.zip"


def run(*arguments: str) -> None:
    subprocess.run(arguments, check=True, creationflags=subprocess.CREATE_NO_WINDOW)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-only", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("This helper is Windows-only; use Docker Compose on other platforms.")
    RUNTIME.mkdir(exist_ok=True)
    binaries = RUNTIME / "pgsql" / "bin"
    if not (binaries / "pg_ctl.exe").exists():
        archive = RUNTIME / "postgresql.zip"
        print(f"Downloading PostgreSQL {VERSION} from the official EDB host...", flush=True)
        urllib.request.urlretrieve(URL, archive)
        checksum = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                # Extract only the server/client runtime, not pgAdmin or bundled extras.
                if not entry.filename.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/")):
                    continue
                target = (RUNTIME / entry.filename).resolve()
                if not target.is_relative_to(RUNTIME.resolve()):
                    raise RuntimeError("Unexpected archive path.")
                bundle.extract(entry, RUNTIME)
        (RUNTIME / "postgresql-source.json").write_text(
            json.dumps({"url": URL, "version": VERSION, "download_sha256": checksum}, indent=2),
            encoding="utf-8",
        )
        archive.unlink()
    if args.download_only:
        return
    cluster = RUNTIME / "pgdata"
    if not (cluster / "PG_VERSION").exists():
        password_file = RUNTIME / "bootstrap-password.txt"
        password_file.write_text("celerity_dev_only\n", encoding="utf-8")
        try:
            run(
                str(binaries / "initdb.exe"),
                "-D",
                str(cluster),
                "-U",
                "celerity",
                f"--pwfile={password_file}",
                "--auth-host=scram-sha-256",
                "--auth-local=trust",
                "--encoding=UTF8",
                "--locale=C",
            )
        finally:
            password_file.unlink(missing_ok=True)
    status = subprocess.run(
        [str(binaries / "pg_ctl.exe"), "status", "-D", str(cluster)],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if status.returncode:
        run(
            str(binaries / "pg_ctl.exe"),
            "start",
            "-D",
            str(cluster),
            "-l",
            str(RUNTIME / "postgres.log"),
            "-o",
            "-h 127.0.0.1 -p 55432",
            "-w",
        )
    environment = os.environ | {"PGPASSWORD": "celerity_dev_only"}
    exists = subprocess.run(
        [
            str(binaries / "psql.exe"),
            "-h",
            "127.0.0.1",
            "-p",
            "55432",
            "-U",
            "celerity",
            "-d",
            "postgres",
            "-tAc",
            "SELECT 1 FROM pg_database WHERE datname='celerity'",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if exists.stdout.strip() != "1":
        subprocess.run(
            [
                str(binaries / "createdb.exe"),
                "-h",
                "127.0.0.1",
                "-p",
                "55432",
                "-U",
                "celerity",
                "celerity",
            ],
            env=environment,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    example = ROOT / ".env.example"
    if not (ROOT / ".env").exists():
        shutil.copyfile(example, ROOT / ".env")
    print("Project-local PostgreSQL is ready at 127.0.0.1:55432.", flush=True)


if __name__ == "__main__":
    main()
