"""Run one CLI inside the already assigned, gated per-call process group."""
import subprocess
import sys

if __name__ == "__main__":
    prompt = sys.stdin.buffer.read()
    try:
        child = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE,
            stdout=sys.stdout.buffer, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        # Reserved bridge code: the external executable was provably not started.
        raise SystemExit(124)
    child.communicate(prompt)
    # Do not pass through external exit codes into the reserved prelaunch code.
    raise SystemExit(0 if child.returncode == 0 else 1)
