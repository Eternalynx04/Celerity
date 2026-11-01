"""Native service entry point with file-based, graceful shutdown for owned processes."""

import argparse
import time
from pathlib import Path
from threading import Thread

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=["api", "shipping"], required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    args = parser.parse_args()
    module = (
        "celerity.main:create_app" if args.service == "api" else "celerity.shipping_stub:create_app"
    )
    server = uvicorn.Server(
        uvicorn.Config(
            module,
            factory=True,
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            log_level="warning",
            timeout_graceful_shutdown=5,
        )
    )

    def watch():
        while not server.should_exit:
            if args.stop_file.exists():
                server.should_exit = True
                return
            time.sleep(0.1)

    Thread(target=watch, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
