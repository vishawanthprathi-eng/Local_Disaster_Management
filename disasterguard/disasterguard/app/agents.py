"""DisasterGuard multi-agent pipeline.

Every agent tries the local Gemma model (via Ollama) first and falls back to a
deterministic rule-based implementation, so the system keeps working even when
Ollama is stopped, the model is missing, or the device is very low on resources.
"""
import json
import math
import os
import re
from pathlib import Path

from . import db, llm

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
EMERGENCY_NUMBER = os.environ.get("DG_EMERGENCY", "112")

DISASTER_TYPES = ["flood", "earthquake", "cyclone", "fire", "heatwave", "landslide", "building_collapse", "medical", "other"]
NEED_KINDS = ["medical", "rescue", "boat", "shelter", "food", "water", "power"]

TYPE_KEYWORDS = {
    "flood": ["flood", "water level", "inundat", "submerged", "overflow", "dam ", "river", "washed away"],
    "earthquake": ["earthquake", "tremor", "quake", "aftershock", "shaking"],
    "cyclone": ["cyclone", "storm", "hurricane", "typhoon", "gale", "tornado", "surge"],
    "fire": ["fire", "blaze", "burning", "smoke", "explosion", "wildfire"],
    "heatwave": ["heatwave", "heat wave", "heat stroke", "heatstroke", "sunstroke"],
    "landslide": ["landslide", "mudslide", "land slide", "rockfall"],
    "building_collapse": ["collapse", "collapsed", "rubble", "debris"],
    "medical": ["injured", "bleeding", "unconscious", "heart attack", "fracture", "medical"],
}
NEED_KEYWORDS = {
    "medical": ["injur", "bleed", "hurt", "unconscious", "medical", "doctor", "ambulance", "hospital", "burn", "fracture", "sick"],
    "rescue": ["trapped", "stuck", "rescue", "stranded", "missing", "buried", "collapse", "rubble", "help"],
    "boat": ["boat", "stranded", "submerged", "flood", "roof"],
    "shelter": ["shelter", "homeless", "homes", "houses", "evacuat", "camp", "displaced", "roof"],
    "food": ["food", "hungry", "hunger", "ration", "starv", "meals"],
    "water": ["drinking water", "no water", "thirst", "water supply", "tanker", "contaminated"],
    "power": ["power", "electricity", "blackout", "no light", "generator"],
}
HIGH_SEVERITY = ["dead", "death", "died", "killed", "fatal", "collapse", "trapped", "buried", "critical", "unconscious", "explosion", "swept", "stranded", "rooftop", "roof top"]
MID_SEVERITY = ["injur", "bleed", "missing", "rising", "spreading", "children", "elderly", "pregnant"]
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twenty": 20, "fifty": 50, "hundred": 100}


# ---------------------------------------------------------------- Triage ---
def _rule_triage(text):
    t = text.lower()
    scores = {k: sum(1 for w in ws if w in t) for k, ws in TYPE_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    dtype = best if scores[best] else "other"

    sev = 1
    if any(w in t for w in MID_SEVERITY):
        sev = 2
    hi = sum(1 for w in HIGH_SEVERITY if w in t)
    if hi:
        sev = 3
    if hi >= 2:
        sev = 4

    people = 0
    for m in re.finditer(r"(\d[\d,]*|" + "|".join(NUM_WORDS) + r")\s+(?:\w+\s+)?(people|persons|families|residents|villagers|children|kids|workers|students|patients|victims|houses|homes)", t):
        raw = m.group(1).replace(",", "")
        n = int(raw) if raw.isdigit() else NUM_WORDS.get(raw, 0)
        if m.group(2) in ("families", "houses", "homes"):
            n *= 4
        people = max(people, n)
    if people >= 50 and sev < 3:
        sev = 3
    if people >= 200:
        sev = 4

    location = ""
    m = re.search(r"\b(?:in|at|near|around|from|of)\s+((?:[A-Z][\w'-]+)(?:\s+[A-Z][\w'-]+){0,3})", text)
    if m:
        location = m.group(1).strip()

    needs = [k for k, ws in NEED_KEYWORDS.items() if any(w in t for w in ws)]
    if dtype == "flood" and "boat" not in needs and sev >= 3:
        needs.append("boat")
    if sev >= 3 and "medical" not in needs and any(w in t for w in ("injur", "dead", "bleed")):
        needs.append("medical")
    if not needs:
        needs = ["rescue"] if sev >= 3 else ["shelter"]

    return {"type": dtype, "severity": sev, "location": location, "people": people,
            "needs": needs, "summary": text.strip()[:140]}


def _normalise_triage(d, fallback):
    out = {}
    t = str(d.get("type", "")).lower().replace(" ", "_")
    out["type"] = t if t in DISASTER_TYPES else fallback["type"]
    try:
        out["severity"] = max(1, min(4, int(d.get("severity"))))
    except (TypeError, ValueError):
        out["severity"] = fallback["severity"]
    out["location"] = str(d.get("location") or fallback["location"] or "").strip()[:80]
    try:
        out["people"] = max(0, int(d.get("people_affected", d.get("people", 0)) or 0))
    except (TypeError, ValueError):
        out["people"] = fallback["people"]
    needs = d.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    needs = [str(n).lower() for n in needs if str(n).lower() in NEED_KINDS]
    out["needs"] = needs or fallback["needs"]
    out["summary"] = str(d.get("summary") or fallback["summary"])[:160]
    return out


TRIAGE_SYSTEM = (
    "You are the Triage Agent of an offline disaster-management system. "
    "Read an emergency report and reply with ONLY a JSON object with keys: "
    f"type (one of {DISASTER_TYPES}), severity (integer 1=low,2=moderate,3=high,4=critical), "
    "location (string, '' if unknown), people_affected (integer, 0 if unknown), "
    f"needs (list chosen from {NEED_KINDS}), summary (one sentence). "
    "Rate severity higher when lives are at immediate risk, people are trapped, or children/elderly are involved."
)


def triage(text):
    fb = _rule_triage(text)
    try:
        raw = llm.generate(f"Emergency report:\n{text}", system=TRIAGE_SYSTEM, json_mode=True, temperature=0.1)
        res = _normalise_triage(llm.parse_json(raw), fb)
        res["engine"] = "gemma"
    except (llm.LLMUnavailable, ValueError):
        res = fb
        res["engine"] = "rules"
    return res


# ------------------------------------------------------------ Allocation ---
def requirements(dtype, severity, people, needs):
    """Translate needs into (resource_kind, quantity) pairs."""
    p = max(people, 10)
    req = []
    for n in needs:
        if n == "medical":
            req.append(("medical", min(4, 1 + people // 50)))
            if severity >= 3:
                req.append(("medical_team", 1 if severity == 3 else 2))
        elif n == "rescue":
            req.append(("rescue", {1: 1, 2: 1, 3: 2, 4: 3}[severity]))
        elif n == "boat":
            req.append(("boat", min(3, max(1, math.ceil(p / 15)))))
        elif n == "shelter":
            req.append(("shelter", p))
        elif n == "food":
            req.append(("food", max(5, math.ceil(p / 4))))
        elif n == "water":
            req.append(("water", max(1, math.ceil(p / 100))))
        elif n == "power":
            req.append(("power", 2 if severity >= 3 else 1))
    return req


def allocate(incident_id, tri):
    allocations, shortfalls = [], []
    for kind, qty in requirements(tri["type"], tri["severity"], tri["people"], tri["needs"]):
        remaining = qty
        for r in db.q("SELECT * FROM resources WHERE kind=? AND available>0 ORDER BY available DESC", (kind,)):
            if remaining <= 0:
                break
            take = min(r["available"], remaining)
            db.run("UPDATE resources SET available=available-? WHERE id=?", (take, r["id"]))
            db.run("INSERT INTO assignments(incident_id,resource_id,qty) VALUES(?,?,?)", (incident_id, r["id"], take))
            allocations.append({"resource": r["name"], "kind": kind, "qty": take, "base": r["base"]})
            remaining -= take
        if remaining > 0:
            shortfalls.append({"kind": kind, "missing": remaining})
    return allocations, shortfalls


def release(incident_id):
    for a in db.q("SELECT * FROM assignments WHERE incident_id=?", (incident_id,)):
        db.run("UPDATE resources SET available=MIN(total, available+?) WHERE id=?", (a["qty"], a["resource_id"]))
    db.run("DELETE FROM assignments WHERE incident_id=?", (incident_id,))


# -------------------------------------------------------------- Planning ---
SEV_LABEL = {1: "Low", 2: "Moderate", 3: "High", 4: "Critical"}


def _rule_plan(tri, allocations, shortfalls):
    lines = [f"PRIORITY: {SEV_LABEL[tri['severity']]} ({tri['type'].replace('_', ' ')}) at {tri['location'] or 'unknown location'}."]
    steps = []
    if tri["severity"] >= 3:
        steps.append(f"Alert district control room and call {EMERGENCY_NUMBER}; treat as life-threatening.")
    for a in allocations:
        steps.append(f"Dispatch {a['qty']} x {a['resource']} from {a['base']}.")
    for s in shortfalls:
        steps.append(f"SHORTFALL: {s['missing']} unit(s) of '{s['kind']}' unavailable; request mutual aid from neighbouring district.")
    if tri["type"] in ("flood", "cyclone"):
        steps.append("Move residents to higher ground / nearest relief camp; cut power to submerged areas.")
    elif tri["type"] in ("earthquake", "building_collapse"):
        steps.append("Establish a cordon, warn of aftershocks, and begin search of debris with trained teams.")
    elif tri["type"] == "fire":
        steps.append("Evacuate the vicinity, shut off gas and electricity, keep access roads clear for fire engines.")
    steps.append("Re-assess the situation in 30 minutes and update this incident.")
    return "\n".join(lines + [f"{i}. {s}" for i, s in enumerate(steps, 1)])


def plan(tri, allocations, shortfalls):
    fallback = _rule_plan(tri, allocations, shortfalls)
    prompt = (
        "Incident:\n" + json.dumps(tri) + "\nResources already allocated:\n" + json.dumps(allocations)
        + "\nShortfalls:\n" + json.dumps(shortfalls)
        + "\n\nWrite a concise numbered action plan (max 7 steps) for the field commander. "
          "Use only the allocated resources; flag shortfalls clearly. No preamble."
    )
    try:
        text = llm.generate(prompt, system="You are the Planning Agent of an offline disaster-response system. Be practical and brief.")
        return text, "gemma"
    except llm.LLMUnavailable:
        return fallback, "rules"


# ---------------------------------------------------------- Orchestrator ---
def process_incident(text):
    text = (text or "").strip()
    if not text:
        raise ValueError("report text is empty")
    tri = triage(text)
    iid = db.run(
        "INSERT INTO incidents(raw_text,type,severity,location,people,needs,summary,engine) VALUES(?,?,?,?,?,?,?,?)",
        (text, tri["type"], tri["severity"], tri["location"], tri["people"], json.dumps(tri["needs"]), tri["summary"], tri["engine"]),
    )
    db.log("Triage", f"{tri['type']} / severity {tri['severity']} / needs {tri['needs']}", tri["engine"], iid)
    allocations, shortfalls = allocate(iid, tri)
    db.log("Resource", f"allocated {len(allocations)} resource line(s); {len(shortfalls)} shortfall(s)", "rules", iid)
    plan_text, engine = plan(tri, allocations, shortfalls)
    db.run("UPDATE incidents SET plan=? WHERE id=?", (plan_text, iid))
    db.log("Planner", "action plan generated", engine, iid)
    return {"id": iid, **tri, "allocations": allocations, "shortfalls": shortfalls, "plan": plan_text, "plan_engine": engine}


# --------------------------------------------------------------- Advisor ---
_chunks = None


def _load_chunks():
    global _chunks
    if _chunks is None:
        _chunks = []
        for f in sorted(KNOWLEDGE_DIR.glob("*.md")):
            title, section = "", ""
            for block in re.split(r"\n(?=#)", f.read_text(encoding="utf-8")):
                head, _, body = block.partition("\n")
                if head.startswith("# "):
                    title = head[2:].strip()
                    continue
                section = head.lstrip("# ").strip()
                if body.strip():
                    _chunks.append({"source": f"{title} - {section}", "text": body.strip()})
    return _chunks


def _tok(s):
    return {w[:5] for w in re.findall(r"[a-z]{3,}", s.lower())}


def retrieve(query, k=3):
    qt = _tok(query)
    scored = []
    for c in _load_chunks():
        ct = _tok(c["source"] + " " + c["text"])
        score = len(qt & ct) + 2 * len(qt & _tok(c["source"]))
        if score:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:k]]


def advise(question):
    question = (question or "").strip()
    if not question:
        raise ValueError("question is empty")
    hits = retrieve(question)
    sources = [h["source"] for h in hits]
    if not hits:
        return {"answer": f"I have no offline guidance for that. If this is an emergency call {EMERGENCY_NUMBER} now.",
                "sources": [], "engine": "rules"}
    ctx = "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits)
    prompt = (f"Safety guidance excerpts:\n{ctx}\n\nQuestion: {question}\n\n"
              f"Answer in short, clear steps using ONLY the excerpts. If the excerpts do not cover it, say so. "
              f"If life is at risk, tell the person to call {EMERGENCY_NUMBER} first.")
    try:
        ans = llm.generate(prompt, system="You are a calm disaster-safety advisor. Never invent medical facts.")
        engine = "gemma"
    except llm.LLMUnavailable:
        ans = "(AI model offline - showing the most relevant stored guidance)\n\n" + ctx
        engine = "rules"
    db.log("Advisor", f"answered: {question[:60]}", engine)
    return {"answer": ans, "sources": sources, "engine": engine}


# -------------------------------------------------------------- Reporter ---
def stats():
    inc = db.q("SELECT * FROM incidents")
    by_sev = {s: 0 for s in (1, 2, 3, 4)}
    by_type = {}
    for i in inc:
        by_sev[i["severity"]] = by_sev.get(i["severity"], 0) + 1
        by_type[i["type"]] = by_type.get(i["type"], 0) + 1
    return {
        "total": len(inc),
        "open": sum(1 for i in inc if i["status"] == "open"),
        "resolved": sum(1 for i in inc if i["status"] == "resolved"),
        "critical_open": sum(1 for i in inc if i["status"] == "open" and i["severity"] == 4),
        "people_affected": sum(i["people"] or 0 for i in inc if i["status"] == "open"),
        "by_severity": by_sev, "by_type": by_type,
    }


def sitrep():
    s = stats()
    res = db.q("SELECT * FROM resources ORDER BY id")
    open_inc = db.q("SELECT * FROM incidents WHERE status='open' ORDER BY severity DESC, id DESC LIMIT 10")
    md = ["# Situation Report", "",
          f"- Open incidents: **{s['open']}** (critical: {s['critical_open']})",
          f"- Resolved: {s['resolved']}", f"- People affected (open): {s['people_affected']}", "",
          "## Priority incidents"]
    md += [f"- #{i['id']} [{SEV_LABEL.get(i['severity'], '?')}] {i['type']} - {i['location'] or 'unknown'}: {i['summary']}" for i in open_inc] or ["- none"]
    md += ["", "## Resources (available / total)"]
    md += [f"- {r['name']}: {r['available']} / {r['total']}" for r in res]
    narrative, engine = "", "rules"
    try:
        narrative = llm.generate(
            "Write a 3-sentence executive summary of this disaster situation for the district collector.\n"
            + json.dumps({"stats": s, "open_incidents": [{k: i[k] for k in ("id", "type", "severity", "location", "people")} for i in open_inc],
                          "resources": [{r["name"]: f"{r['available']}/{r['total']}"} for r in res]}),
            system="You write precise, neutral situation reports.")
        engine = "gemma"
    except llm.LLMUnavailable:
        pass
    if narrative:
        md[2:2] = ["## Executive summary", narrative, ""]
    db.log("Reporter", "situation report generated", engine)
    return {"markdown": "\n".join(md), "stats": s, "engine": engine}
