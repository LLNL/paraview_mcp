"""
M0 spike test client for paraview_mcp -- stdlib only, no ParaView needed.

Run from a plain terminal while bridge/spike/m0_bridge_spike.py is running
inside the ParaView Python Shell (see docs/M0_SPIKE.md for the full
procedure). This just drives the four M0 checkpoints and prints what to
look for; the reentry and vtk-message checks require you to also look at
the ParaView Python Shell / Output Messages panel, they can't be judged
from this client's output alone.

Usage:
    python3 m0_client.py ping
    python3 m0_client.py reentry
    python3 m0_client.py state50
    python3 m0_client.py servererror   # scenario 2 (pvserver) only
    python3 m0_client.py all           # ping + reentry + state50
"""
import json
import socket
import sys
import uuid

HOST = "127.0.0.1"
PORT = 9911


def send(op, timeout=30, **fields):
    req = {"id": str(uuid.uuid4())[:8], "op": op, **fields}
    with socket.create_connection((HOST, PORT), timeout=10) as s:
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        s.settimeout(timeout)
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("bridge closed the connection without a full response")
            buf += chunk
        line, _, _ = buf.partition(b"\n")
    return json.loads(line.decode("utf-8"))


def check_ping():
    print("--- ping ---")
    resp = send("ping")
    print(json.dumps(resp, indent=2))
    assert resp["status"] == "ok"
    print("PASS: bridge responds over TCP and the request/response round-trip works.\n")


def check_reentry():
    print("--- (b) reentrancy: exec a ~2s loop that sleeps + Render()s ---")
    code = (
        "import time\n"
        "for _ in range(10):\n"
        "    time.sleep(0.2)\n"
        "    try:\n"
        "        simple.Render()\n"
        "    except Exception:\n"
        "        pass\n"
        "'done'"
    )
    resp = send("exec", code=code, render=False, timeout=15)
    print(json.dumps(resp, indent=2))
    print("Now look at the ParaView Python Shell output produced WHILE this call ran:")
    print("  - If you see '*** REENTRY DETECTED ***' lines: a TimerEvent fired inside")
    print("    another TimerEvent. Record depth reached. The reentry guard in")
    print("    DESIGN.md 4.1 is confirmed necessary -- keep it.")
    print("  - If you see none: no reentrancy under this workload. Record as such;")
    print("    the guard stays in as a safety net but wasn't exercised here.\n")


def check_state50():
    print("--- (d) create 50 sources, then time the state summary ---")
    code = (
        "for i in range(50):\n"
        "    s = Sphere(Radius=1.0)\n"
        "    s.ThetaResolution = 6\n"
        "'created'"
    )
    resp = send("exec", code=code, render=False, timeout=30)
    if not resp.get("ok", True):
        print(json.dumps(resp, indent=2))
        print("FAIL: could not create 50 sources, see error above.\n")
        return
    resp = send("state")
    state = resp["state"]
    print(json.dumps({k: v for k, v in state.items() if k != "sources"}, indent=2))
    print("count=%d elapsed_ms=%s" % (state["count"], state["elapsed_ms"]))
    print("PASS if well under the ~20ms budget from DESIGN.md 6.5; otherwise "
          "record the real number, the budget/approach may need revisiting.\n")


def check_server_error():
    print("--- (c) provoke a VTK error via a malformed file (scenario 2 / pvserver only) ---")
    print("Make sure the ParaView GUI is connected to a pvserver (File > Connect)")
    print("before running this -- see docs/M0_SPIKE.md scenario 2, step 2.\n")
    # The header must pass OpenDataFile()'s own Python-level file-type
    # sniffing (it looks for the "# vtk DataFile Version" signature) so
    # that it actually constructs a legacy VTK reader instead of raising
    # a Python RuntimeError ("no reader found") before any VTK C++ code
    # runs at all -- that's what a plain-garbage file triggers, and it
    # never touches vtkOutputWindow. Corrupt the body instead of the
    # header so parsing gets far enough to hit a real vtkErrorMacro call.
    code = (
        "import os, tempfile\n"
        "bad = os.path.join(tempfile.gettempdir(), 'm0_bad2.vtk')\n"
        "with open(bad, 'w') as f:\n"
        "    f.write('# vtk DataFile Version 3.0\\n')\n"
        "    f.write('m0 spike corrupt file\\n')\n"
        "    f.write('ASCII\\n')\n"
        "    f.write('DATASET POLYDATA\\n')\n"
        "    f.write('POINTS not_a_number float\\n')\n"
        "src = OpenDataFile(bad)\n"
        "try:\n"
        "    src.UpdatePipeline()\n"
        "except Exception as e:\n"
        "    str(e)\n"
        "'reached'\n"
    )
    resp = send("exec", code=code, render=False, timeout=15)
    print(json.dumps(resp, indent=2))
    print("Compare 'vtk_messages' / 'stdout' / 'stderr' above against the ParaView")
    print("GUI's own Output Messages panel (View > Output Messages) for the same error.")
    print("  - Error text present in vtk_messages: (c) PASS, server-side VTK errors")
    print("    reach this process's vtkOutputWindow.")
    print("  - Output Messages panel shows the error but vtk_messages is empty:")
    print("    (c) FAIL -- there's a structural wall between the client-server RMI")
    print("    relay and vtkOutputWindow substitution. Important negative result,")
    print("    record it against DESIGN.md 6.3.")
    print("  - Neither shows anything: the trigger itself is too weak, try a")
    print("    different malformed input by hand before concluding either way.\n")


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    try:
        if what in ("ping", "all"):
            check_ping()
        if what in ("reentry", "all"):
            check_reentry()
        if what in ("state50", "all"):
            check_state50()
        if what == "servererror":
            check_server_error()
        if what not in ("ping", "reentry", "state50", "servererror", "all"):
            print(__doc__)
    except (ConnectionRefusedError, socket.timeout) as e:
        print("Could not reach the bridge at %s:%d (%s)." % (HOST, PORT, e))
        print("Is m0_bridge_spike.py running in the ParaView Python Shell?")
        sys.exit(1)


if __name__ == "__main__":
    main()
