#!/usr/bin/env python3
"""Windows-to-WSL Firecracker wrapper for SAP."""

import os
import subprocess
import sys


def win_to_wsl(path: str) -> str:
    """Convert a Windows absolute path to a WSL /mnt/ path."""
    if not path or len(path) < 2:
        return path
    if path[1] == ":":
        drive = path[0].lower()
        rest = path[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return path.replace("\\", "/")


def main() -> int:
    args = sys.argv[1:]
    wsl_args = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--api-sock", "--config-file") and i + 1 < len(args):
            wsl_args.append(arg)
            wsl_args.append(win_to_wsl(args[i + 1]))
            i += 2
        else:
            wsl_args.append(arg)
            i += 1

    env = os.environ.copy()
    env["PATH"] = "/home/mindy/.local/bin:" + env.get("WSL_PATH", "/usr/local/bin:/usr/bin:/bin")

    cmd = ["wsl", "firecracker"] + wsl_args
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Write our PID so the parent can kill us; Firecracker inside WSL will have its own PID tree.
    # We just need to keep this wrapper alive so the socket file isn't cleaned up.
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
    return proc.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
