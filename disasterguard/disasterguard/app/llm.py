"""Thin client for a local Ollama server (default model: Gemma). Stdlib only."""
import json
import re
import urllib.request
import urllib.error

from . import config


class LLMUnavailable(Exception):
    pass


def _request(path, payload=None, timeout=5):
    url = config.OLLAMA_HOST + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        raise LLMUnavailable(str(e))


def status():
    """Return {online, model, model_installed, models}."""
    try:
        tags = _request("/api/tags", timeout=2)
        names = [m.get("name", "") for m in tags.get("models", [])]
        want = config.OLLAMA_MODEL
        installed = want in names or (":" not in want and any(n.split(":")[0] == want for n in names))
        return {"online": True, "model": want, "model_installed": installed, "models": names}
    except LLMUnavailable:
        return {"online": False, "model": config.OLLAMA_MODEL, "model_installed": False, "models": []}


def generate(prompt, system=None, json_mode=False, temperature=0.2):
    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system
    if json_mode:
        payload["format"] = "json"
    out = _request("/api/generate", payload, timeout=config.LLM_TIMEOUT)
    text = out.get("response", "").strip()
    if not text:
        raise LLMUnavailable("empty response")
    return text


def parse_json(text):
    """Parse JSON from model output, tolerating code fences / extra prose."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except ValueError:
            pass
    raise ValueError("no JSON found in model output")
