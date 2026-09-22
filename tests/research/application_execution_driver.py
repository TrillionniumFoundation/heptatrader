#!/usr/bin/env python3
"""Test-only process consumer of source OR relocated installed application SDK."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True
if len(sys.argv) != 8:
    raise SystemExit("adapter native root socket token operation key required")
adapter, native, root, socket, token, operation, key = sys.argv[1:]
spec = importlib.util.spec_from_file_location("application_sdk", adapter)
g = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = g
spec.loader.exec_module(g)
transport = g.NativeStrategyTransport(native, socket, token, 3000)
store = g.ApplicationStore(root + "/applications")
gateway = g.StrategyGateway(transport, store)
try:
    if operation == "prepare":
        intent = g.LimitIntent("EUR.USD", "EUR", "CASH", "SIM", "USD", "BUY", "7", "1.1", "1.1001",
                               time.time_ns() // 1000000 + 120000)
        response = gateway.prepare(key, intent)
        print("COMMAND " + response["command_id"], flush=True)
    elif operation == "race":
        children = [subprocess.Popen([sys.executable, "-I", "-B", __file__, adapter, native, root, socket,
                                      token, "submit", key], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for _ in range(4)]
        try:
            results = [child.communicate(timeout=8) + (child.returncode,) for child in children]
            if sorted(value[2] for value in results) != [0, 0, 0, 2]:
                raise RuntimeError("unexpected race results: " + repr(results))
            for stdout, _, code in results:
                if code == 0 and json.loads(stdout)["tool"] != "execution.get_command_status":
                    raise RuntimeError("repeated application submit was not a status query")
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill(); child.wait()
        print("APPLICATION_RACE_PASS", flush=True)
    elif operation == "mark-crash":
        original = transport.call
        def intercepted(op, **kw):
            if op == "submit":
                os.kill(os.getpid(), signal.SIGKILL)
            return original(op, **kw)
        transport.call = intercepted
        gateway.submit(key)
        raise RuntimeError("client did not die at durable pre-send boundary")
    elif operation in ("submit", "inspect"):
        response = getattr(gateway, operation)(key)
        print(json.dumps(response), flush=True)
    else:
        raise ValueError("unknown test operation")
except (OSError, ValueError, RuntimeError, TypeError) as error:
    print(str(error), file=sys.stderr, flush=True)
    raise SystemExit(2)
