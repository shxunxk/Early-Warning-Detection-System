"""Local LLM (Ollama) risk summaries with a number-grounding check and template fallback.

The LLM only explains the evidence. It never computes or changes a score or tier.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
# Small models first: summaries are short and grounded, so 3-4B is the speed/quality sweet spot.
PREFERRED = ["qwen3:4b-instruct", "llama3.2:3b", "qwen3:8b", "llama3.1:8b"]


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
        # Sign-insensitive: "a balance erosion of 76%" correctly restates "-76%".
        if not any(abs(abs(n) - abs(a)) <= max(0.6, 0.01 * abs(a)) for a in allowed):
            bad.append(n)
    return not bad, bad


SYSTEM = (
    "You write early-warning credit risk notes for bank relationship managers. "
    "Tiers from least to most severe: Green (no concern), Watch, Amber, Red. "
    "Risk score is 0-100 and higher means riskier. "
    "Use only the facts given. Never invent numbers, dates, causes or customer details. "
    "Copy numbers exactly as written in the facts. Do not repeat the customer ID. "
    "No preamble, no headings, no bullet points.")


def facts_for(ev: dict, reasons: list[str], action: str = "") -> dict:
    f = dict(tier=ev["tier"], risk_score=f"{ev['risk_score']:.0f}/100",
             confidence=ev["confidence"], days_past_due=int(ev["current_dpd"]),
             risk_drivers=reasons[:5] or ["No risk signals are firing"])
    if ev.get("mitigation", 0) < 0:
        f["mitigation"] = "One-off delay with recovery and clean history reduced the score"
    if ev.get("mitigation_blocked"):
        f["mitigation"] = "One-off delay NOT mitigated because underlying stress exists"
    if ev.get("trigger"):
        f["hard_trigger"] = ev["trigger"]
    if ev["confidence"] == "Low":
        f["data_note"] = "Limited data; missing information is not treated as risk"
    if action:
        f["policy_action_for_tier"] = action
    return f


def llm_summary(cid: str, ev: dict, reasons: list[str], model: str, action: str = "") -> dict:
    facts = facts_for(ev, reasons, action)
    prompt = ("Write 3-5 sentences explaining this customer's risk position and what is "
              "driving it, then a final line starting 'Suggested action:' based on "
              "policy_action_for_tier.\n\nFACTS:\n" + json.dumps(facts, indent=1))
    body = json.dumps(dict(model=model, system=SYSTEM, prompt=prompt, stream=False, think=False,
                           options=dict(temperature=0.1, num_predict=300))).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.load(r)
    text = re.sub(r"<think>.*?</think>", "", res.get("response", ""), flags=re.S).strip()
    ok, bad = grounded(text, {"cid": cid, **facts}, reasons)
    return dict(text=text, grounded=ok, ungrounded_numbers=bad, model=model,
                prompt_tokens=res.get("prompt_eval_count"), output_tokens=res.get("eval_count"),
                seconds=round(res.get("total_duration", 0) / 1e9, 1))
