"""Probe Gemini-API models for NATIVE function calling (not text-generated JSON). One request per probe.

Checks both the OpenAI-compatible endpoint (used by llm_agent.py) and the native generateContent API.
Prints full error bodies so quota type (per-minute vs per-day) is visible. Key read from ../.env.
"""

import json
import sys
import urllib.error
import urllib.request

import paypal_smoke as smoke

BASE = "https://generativelanguage.googleapis.com/v1beta"
PROMPT = "Refund $0.25 on capture 8AB12345CD678901E. Use the tool."
REFUND_SCHEMA = {"type": "object", "properties": {"capture_id": {"type": "string"}, "amount": {"type": "string"}},
                 "required": ["capture_id", "amount"]}


def post(url: str, key: str, body: dict) -> tuple[int, dict]:
    # OpenAI-compat wants Bearer; native API rejects an API key sent as Bearer, so send only x-goog-api-key there
    auth = {"Authorization": f"Bearer {key}"} if "/openai/" in url else {"x-goog-api-key": key}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **auth})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")
    except TimeoutError:
        return 504, {"error": "client timeout after 180s"}


def probe_openai_compat(model: str, key: str) -> None:
    status, data = post(f"{BASE}/openai/chat/completions", key, {
        "model": model, "temperature": 0, "messages": [{"role": "user", "content": PROMPT}],
        "tools": [{"type": "function", "function": {"name": "create_refund", "description": "Refund a capture.",
                                                    "parameters": REFUND_SCHEMA}}]})
    report("openai-compat", model, status, data, native=lambda d: d["choices"][0]["message"].get("tool_calls"),
           text=lambda d: d["choices"][0]["message"].get("content"))


def probe_native(model: str, key: str) -> None:
    status, data = post(f"{BASE}/models/{model}:generateContent", key, {
        "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
        "tools": [{"functionDeclarations": [{"name": "create_refund", "description": "Refund a capture.",
                                             "parameters": REFUND_SCHEMA}]}],
        "generationConfig": {"temperature": 0}})

    def parts(d: dict) -> list:
        return d["candidates"][0]["content"].get("parts", [])
    report("native", model, status, data, native=lambda d: [p["functionCall"] for p in parts(d) if "functionCall" in p],
           text=lambda d: " ".join(p.get("text", "") for p in parts(d)))


def report(interface: str, model: str, status: int, data: dict, native, text) -> None:
    if status != 200:
        first = data[0] if isinstance(data, list) and data else data
        err = first.get("error", first) if isinstance(first, dict) else first
        print(f"{interface:<14} {model:<22} HTTP {status}  {json.dumps(err)[:700]}")
        return
    try:
        calls, body = native(data), text(data)
    except (KeyError, IndexError, TypeError) as exc:
        print(f"{interface:<14} {model:<22} HTTP 200  unparseable ({exc}): {json.dumps(data)[:300]}")
        return
    print(f"{interface:<14} {model:<22} HTTP 200  native_tool_call={bool(calls)} calls={json.dumps(calls)[:200]} "
          f"text={str(body)[:150]!r}")


def main() -> None:
    key = smoke.load_env(smoke.ENV_PATH).get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY missing in ../.env")
    for model in sys.argv[1:]:
        probe_openai_compat(model, key)
        probe_native(model, key)


if __name__ == "__main__":
    main()
