import argparse
import os
import sys
from pathlib import Path

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Hermes Novel Translation System")
    parser.add_argument("--no-login", action="store_true", help="Start in anonymous mode without session cookies")
    parser.add_argument("--port", type=int, default=None, help="Port to bind the server")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host address to bind")
    parser.add_argument("--headless", action="store_true", help="Run Chromium in headless mode (default: headful under Xvfb/display)")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright launch (for mock / tests)")
    args, _ = parser.parse_known_args()

    if args.no_login:
        os.environ["HERMES_NO_LOGIN"] = "1"
    if args.headless:
        os.environ["HERMES_HEADLESS"] = "1"
    if args.skip_browser:
        os.environ["HERMES_SKIP_BROWSER"] = "1"
    if args.port:
        os.environ["ADAPTER_PORT"] = str(args.port)

    from .core.config import ADAPTER_PORT

    port = args.port or ADAPTER_PORT

    package_dir = Path(__file__).parent
    sys.path.insert(0, str(package_dir.parent))

    uvicorn.run(
        "hermes_chatgpt_web.api.app:app",
        host=args.host,
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
