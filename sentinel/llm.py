"""Local LLM (Ollama) risk summaries with a number-grounding check and template fallback.

The LLM only explains the evidence. It never computes or changes a score or tier.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
PREFERRED = ["qwen3:8b", "llama3.1:8b", "qwen2.5:7b", "llama3.2:3b"]


def available_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=1.5) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return []


def pick_model(models: list[str]) -> str | None:
    for p in PREFERRED:
        if p in models:
            return p
    return models[0] if models else None


def template_summary(cid: str, ev: dict, reasons: list[str], name: str = "") -> str:
    fired = [r for r in reasons][:4]
    tier, score, conf = ev["tier"], ev["risk_score"], ev["confidence"]
    lines = [f"{name or cid} is rated {tier} with a risk score of {score:.0f}/100 "
             f"({conf.lower()} confidence)."]
    if fired:
        lines.append("Main drivers: " + "; ".join(fired) + ".")
    else:
        lines.append("No risk signals are currently firing.")
    if ev.get("mitigation", 0) < 0:
        lines.append(f"A one-off delay was mitigated ({ev['mitigation']:.0f} points) because "
                     "the customer recovered with a clean history.")
    if ev.get("mitigation_blocked"):
        lines.append("Mitigation was not applied because underlying cash-flow or exposure "
                     "stress is present.")
    if ev.get("trigger"):
        lines.append(f"Hard trigger applied: {ev['trigger']}.")
    if conf == "Low":
        lines.append("Data is limited, so missing information was not treated as risk.")
    return " ".join(lines)


def _numbers(text: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*\.?\d*", text)]


def grounded(text: str, ev: dict, reasons: list[str]) -> tuple[bool, list[float]]:
    allowed = _numbers(json.dumps(ev)) + _numbers(" ".join(reasons))
    bad = []
    for n in _numbers(text):
        if abs(n) <= 12:  # small counts / months are fine
            continue
        if not any(abs(n - a) <= max(0.6, 0.01 * abs(a)) for a in allowed):
            bad.append(n)
    return not bad, bad


def llm_summary(cid: str, ev: dict, reasons: list[str], model: str) -> dict:
    fired = [s for s in ev["signals"] if s["points"]]
    facts = dict(customer=cid, tier=ev["tier"], risk_score=ev["risk_score"],
                 confidence=ev["confidence"], current_dpd=ev["current_dpd"],
                 mitigation=ev["mitigation"], trigger=ev["trigger"], indices=ev["indices"],
                 fired_signals=[{k: s[k] for k in ("name", "value", "operator", "threshold",
                                                    "points")} for s in fired])
    prompt = (
        "You are a credit risk analyst assistant at a bank. Write a 4-6 sentence early "
        "warning summary for a relationship manager, then one line starting 'Suggested "
        "action:'. Use ONLY the facts in the JSON. Do not invent numbers, dates or causes. "
        "Quote numbers exactly as given. Higher score = higher risk.\n\nFACTS:\n"
        + json.dumps(facts, default=str))
    body = json.dumps(dict(model=model, prompt=prompt, stream=False, think=False,
                           options=dict(temperature=0.1, num_predict=350))).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = json.load(r).get("response", "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    ok, bad = grounded(text, ev, reasons)
    return dict(text=text, grounded=ok, ungrounded_numbers=bad, model=model)
