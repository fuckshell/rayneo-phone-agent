"""Launch one Windows worker without creating a console window."""
import json
import subprocess
import sys


def main():
    spec = json.load(sys.stdin)
    with open(spec["stdout"], "ab", buffering=0) as out, open(
        spec["stderr"], "ab", buffering=0
    ) as err:
        child = subprocess.Popen(
            '"' + spec["executable"] + '" ' + spec["arguments"],
            cwd=spec["directory"],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )
    print(child.pid)


if __name__ == "__main__":
    main()
