import json
import sqlite3
import threading
from pathlib import Path

from . import config

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT DEFAULT (datetime('now')),
  raw_text TEXT NOT NULL,
  type TEXT, severity INTEGER, location TEXT, people INTEGER DEFAULT 0,
  needs TEXT DEFAULT '[]', summary TEXT, plan TEXT,
  status TEXT DEFAULT 'open', engine TEXT
);
CREATE TABLE IF NOT EXISTS resources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, kind TEXT, total INTEGER, available INTEGER, base TEXT
);
CREATE TABLE IF NOT EXISTS assignments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER, resource_id INTEGER, qty INTEGER
);
CREATE TABLE IF NOT EXISTS agent_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT (datetime('now')),
  incident_id INTEGER, agent TEXT, message TEXT, engine TEXT
);
"""

# (name, kind, total, base)
SEED_RESOURCES = [
    ("Ambulances", "medical", 8, "District Hospital"),
    ("Medical Teams", "medical_team", 5, "District Hospital"),
    ("Rescue Teams", "rescue", 10, "Fire Station"),
    ("Rescue Boats", "boat", 6, "River Control Post"),
    ("Relief Shelter Beds", "shelter", 600, "Govt School Camps"),
    ("Food Kits (family of 4)", "food", 300, "Central Warehouse"),
    ("Water Tankers", "water", 6, "Municipal Depot"),
    ("Portable Generators", "power", 10, "Electricity Board Yard"),
]


def conn():
    global _conn
    with _lock:
        if _conn is None:
            Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            if _conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 0:
                _conn.executemany(
                    "INSERT INTO resources(name,kind,total,available,base) VALUES(?,?,?,?,?)",
                    [(n, k, t, t, b) for n, k, t, b in SEED_RESOURCES],
                )
                _conn.commit()
        return _conn


def close():
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def run(sql, args=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def log(agent, message, engine="rules", incident_id=None):
    run("INSERT INTO agent_logs(incident_id,agent,message,engine) VALUES(?,?,?,?)",
        (incident_id, agent, message, engine))


def incident_row(r):
    r["needs"] = json.loads(r.get("needs") or "[]")
    return r
