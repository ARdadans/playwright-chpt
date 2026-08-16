import os
import sys
from pathlib import Path


def dev():
    # Set HERMES_ENV to dev
    os.environ["HERMES_ENV"] = "dev"
    if "--no-login" in sys.argv:
        os.environ["HERMES_NO_LOGIN"] = "1"

    if "CHATGPT_HOME" not in os.environ:
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        data_dir = project_root / ".data" / "dev"
        data_dir.mkdir(exist_ok=True, parents=True)
        os.environ["CHATGPT_HOME"] = str(data_dir)
    else:
        data_dir = Path(os.environ["CHATGPT_HOME"])

    print(f"Running in DEV mode with CHATGPT_HOME={data_dir}", flush=True)


    from hermes_chatgpt_web.main import main

    main()


if __name__ == "__main__":
    dev()
