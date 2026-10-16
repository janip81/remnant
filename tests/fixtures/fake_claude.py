"""Fake `claude -p --input-format stream-json` for the benchmark harness tests.

FAKE_MODE: ok (default) | error | nocost | hang | crash. Emits the event shapes the
harness reads: system init, text delta, assistant tool_use, result with usage.
"""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_MODE", "ok")
if "--version" in sys.argv:
    print("0.0.0 (fake)")
    sys.exit(0)
sid = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else "sess-1"
print(json.dumps({"type": "system", "subtype": "init", "session_id": sid}), flush=True)
for n, line in enumerate(sys.stdin):
    prompt = json.loads(line)["message"]["content"]
    if mode == "hang":
        time.sleep(60)
    if mode == "crash" and n == 1:
        sys.exit(3)
    print(json.dumps({"type": "stream_event", "session_id": sid,
                      "event": {"delta": {"type": "text_delta", "text": " "}}}), flush=True)     # blank: not "visible"
    time.sleep(0.02)
    print(json.dumps({"type": "stream_event", "session_id": sid,
                      "event": {"delta": {"type": "text_delta", "text": "Answer"}}}), flush=True)
    print(json.dumps({"type": "assistant", "session_id": sid, "message": {"content": [
        {"type": "tool_use", "name": "mcp__remnant__search_memory"}, {"type": "text", "text": "x"}]}}), flush=True)
    result = {"type": "result", "session_id": sid, "is_error": mode == "error", "result": f"Answer to: {prompt}",
              "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000,
                        "output_tokens": 5, "output_tokens_details": {"thinking_tokens": 3}},
              "total_cost_usd": 0.01, "duration_ms": 40, "num_turns": 1}
    if mode == "nocost":
        del result["total_cost_usd"]
        del result["usage"]["cache_read_input_tokens"]
    print(json.dumps(result), flush=True)
