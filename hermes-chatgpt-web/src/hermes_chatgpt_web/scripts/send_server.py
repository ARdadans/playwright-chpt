#!/usr/bin/env python3
"""
Create a zip of the current project (respecting .gitignore),
send it via SCP to a server, and extract it to:
  /home/dadan/{project_name}/{extract_dir}

Set defaults via variables below or use CLI args.
"""

import argparse
import fnmatch
import os
import platform
import subprocess
import sys
import tempfile
import zipfile

DEFAULT_SERVER = "ddn@43.153.154.115"
DEFAULT_EXTRACT_DIR = None
DEFAULT_REMOTE_BASE = "/home/ddn/test"
DEFAULT_SOURCE = "."
DEFAULT_SSH_PASSWORD_ENV = "4y5WzPHPAfH7zIp"


def parse_gitignore(gitignore_path):
    patterns = []
    with open(gitignore_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns


def get_ssh_password():
    return os.environ.get(DEFAULT_SSH_PASSWORD_ENV)


def _cmd_with_sshpass(cmd, password):
    if not password:
        return cmd
    if platform.system().lower() == "windows":
        plink = None
        pscp = None
        for name in ["plink", "plink.exe"]:
            try:
                plink = (
                    subprocess.check_output(["where", name], shell=True, text=True, stderr=subprocess.DEVNULL)
                    .strip()
                    .splitlines()[0]
                    .strip('"')
                    .strip("'")
                )
                if os.path.isfile(plink):
                    break
                plink = None
            except Exception:
                pass
        for name in ["pscp", "pscp.exe"]:
            try:
                pscp = (
                    subprocess.check_output(["where", name], shell=True, text=True, stderr=subprocess.DEVNULL)
                    .strip()
                    .splitlines()[0]
                    .strip('"')
                    .strip("'")
                )
                if os.path.isfile(pscp):
                    break
                pscp = None
            except Exception:
                pass
        if plink or pscp:
            out = list(cmd)
            if out and out[0] == "scp" and pscp:
                return [pscp, "-pw", password, *out[1:]]
            if out and out[0] == "ssh" and plink:
                return [plink, "-batch", "-pw", password, *out[1:]]
        return cmd
    return ["sshpass", "-p", password, *cmd]


def run(cmd, check=True, password=None):
    cmd = _cmd_with_sshpass(cmd, password)
    print(f"[RUN] {' '.join('***' if 'pass' in c.lower() or 'password' in c.lower() else c for c in cmd)}")
    return subprocess.run(cmd, check=check)


def is_gitignored(path, patterns, base_dir):
    rel = os.path.relpath(path, base_dir)
    name = os.path.basename(path)

    for pattern in patterns:
        if pattern.endswith("/"):
            dir_pat = pattern.rstrip("/")
            if rel == dir_pat or rel.startswith(dir_pat + os.sep):
                return True
        if fnmatch.fnmatch(name, pattern):
            return True
        if fnmatch.fnmatch(rel, pattern):
            return True
        if "/" in pattern:
            prefix = pattern.split("*")[0].split("?")[0]
            if rel.startswith(prefix):
                return True
    return False


def create_zip(source_dir, output_zip, gitignore_path):
    patterns = parse_gitignore(gitignore_path)
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if not is_gitignored(os.path.join(root, d), patterns, source_dir)]
            for file in files:
                fp = os.path.join(root, file)
                if is_gitignored(fp, patterns, source_dir):
                    continue
                arcname = os.path.relpath(fp, source_dir)
                zf.write(fp, arcname)
    return output_zip


def main():
    parser = argparse.ArgumentParser(description="SCP zip to server and extract")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"user@host (default: {DEFAULT_SERVER})")
    parser.add_argument(
        "--extract-dir",
        default=DEFAULT_EXTRACT_DIR,
        help="nama folder extract (default: langsung di dalam project folder)",
    )
    parser.add_argument(
        "--remote-base", default=DEFAULT_REMOTE_BASE, help=f"base path di server (default: {DEFAULT_REMOTE_BASE})"
    )
    parser.add_argument(
        "--source", default=DEFAULT_SOURCE, help="source folder yang akan di-zip (default: current dir)"
    )
    parser.add_argument(
        "--password", default=None, help="SSH password (alternatif: set env " + DEFAULT_SSH_PASSWORD_ENV + ")"
    )
    args = parser.parse_args()

    password = args.password or get_ssh_password()
    if password:
        src = "CLI arg" if args.password else f"env {DEFAULT_SSH_PASSWORD_ENV}"
        print(f"[INFO] Using SSH password from {src}")

    source_dir = os.path.abspath(args.source)
    project_name = os.path.basename(source_dir)
    if args.extract_dir:
        remote_dir = f"{args.remote_base}/{project_name}/{args.extract_dir}"
    else:
        remote_dir = f"{args.remote_base}/{project_name}"

    gitignore_path = os.path.join(source_dir, ".gitignore")
    if not os.path.exists(gitignore_path):
        print("Error: .gitignore tidak ditemukan di source folder", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        zip_name = f"{project_name}.zip"
        zip_path = os.path.join(tmp, zip_name)
        print(f"[1/4] Creating zip: {zip_path}")
        create_zip(source_dir, zip_path, gitignore_path)
        zip_size = os.path.getsize(zip_path)
        print(f"      Size: {zip_size:,} bytes")

        print(f"[2/4] Creating remote dir: {remote_dir}")
        run(["ssh", args.server, f"mkdir -p {remote_dir}"], password=password)

        print("[3/4] Uploading zip via SCP...")
        run(["scp", zip_path, f"{args.server}:{remote_dir}/"], password=password)

        print("[4/4] Extracting on server...")
        run(["ssh", args.server, f"cd {remote_dir} && unzip -o {zip_name} && rm {zip_name}"], password=password)

        print(f"Done. Extracted to: {remote_dir}")


if __name__ == "__main__":
    main()
