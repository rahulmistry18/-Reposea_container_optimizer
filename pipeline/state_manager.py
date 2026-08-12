"""
REPOSEA STATE MANAGER
===================================
Persistent fleet state across every hourly pipeline run.

Each container lives through a real lifecycle:
  GATE_IN → AT_SEA → ARRIVED → IN_FREE_DAYS → OVERDUE → COMPLETE → replaced

On every pipeline run:
  1. Load fleet_state.json from disk
  2. Advance every container by elapsed real time
  3. Auto-transition stages (AT_SEA→ARRIVED when transit hours elapsed, etc.)
  4. Replace COMPLETE containers with fresh spawns
  5. Save state back to disk

Ledger (container closure tracking):
  container_ledger.json      PRIMARY ledger — every container ever tracked,
                              both active and resolved, one record each,
                              upserted every run. Source of truth.
  completed_containers.json  FINAL ledger — resolved-only, always fetched/
                              derived from the primary ledger (never written
                              independently), so the two can't drift apart.

Initial seed gives a realistic mix:
  15% GATE_IN · 40% AT_SEA · 5% ARRIVED · 25% IN_FREE_DAYS · 15% OVERDUE
"""

import json
import random
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

STATE_DIR  = Path(__file__).parent.parent / "data" / "state"
STATE_FILE = STATE_DIR / "fleet_state.json"
STATE_DIR.mkdir(parents=True, exist_ok=True)

CLOSURE_LOG_FILE = STATE_DIR / "completed_containers.json"   # final ledger — resolved only, derived from LEDGER_FILE
LEDGER_FILE      = STATE_DIR / "container_ledger.json"        # primary ledger — every container, active + resolved
MAX_CLOSURE_LOG_ENTRIES = 500

FLEET_SIZE = 35

# A container's case is deterministically closed out (marked COMPLETE, logged,
# and replaced by a fresh spawn) once it's been overdue long enough to have
# received its 2-week escalation email (pipeline/alerts.py ESCALATION_THRESHOLD_DAYS)
# plus a further grace period of at most 1 week for the responsible person to
# act — after that, the case is considered handled and the fleet moves on.
# Keep this in sync with pipeline/alerts.py if that threshold ever changes.
ESCALATION_THRESHOLD_DAYS  = 14   # when the first overdue email goes out
RESOLUTION_GRACE_DAYS      = 7    # max additional time before the case is closed
CLOSURE_AFTER_OVERDUE_DAYS = ESCALATION_THRESHOLD_DAYS + RESOLUTION_GRACE_DAYS  # 21

# ── Reference tables ──────────────────────────────────────────────────────────

TRANSIT_HOURS = {
    "TRANS_PAC":  (264, 408),
    "TRANS_ATL":  (192, 312),
    "ASIA_EUR":   (480, 600),
    "INTRA_ASIA": (48,  120),
}

FREE_DAYS = {
    "One-Way":      (7,  14),
    "Master Lease": (14, 21),
    "Long-Term":    (21, 35),
}

PER_DIEM = {
    "One-Way":      (125, 175),
    "Master Lease": (70,  100),
    "Long-Term":    (30,   55),
}

ROUTES = {
    "TRANS_PAC": [
        ("CNSHA","USLAX"),("CNSHA","USLONG"),("CNSHA","USSEA"),
        ("CNYTN","USLAX"),("CNQIN","USOAK"),("TWKHH","USSEA"),
        ("HKHKG","USLAX"),("JPYOK","USSEA"),("KRPUS","USLONG"),
    ],
    "TRANS_ATL": [
        ("NLRTM","USNYC"),("DEHAM","USNYC"),("BEANR","USNYC"),
        ("DEHAM","USBAL"),("GBFXT","USBOS"),("NLRTM","USSAV"),
        ("FRFOS","USNYC"),("ESBCN","USNYC"),
    ],
    "ASIA_EUR": [
        ("CNSHA","NLRTM"),("CNSHA","DEHAM"),("CNYTN","DEHAM"),
        ("CNSHA","BEANR"),("KRPUS","NLRTM"),("JPYOK","NLRTM"),
        ("SGSIN","GBFXT"),("CNSHA","GBFXT"),
    ],
    "INTRA_ASIA": [
        ("CNSHA","SGSIN"),("CNSHA","THLCH"),("TWKHH","SGSIN"),
        ("HKHKG","PHMNL"),("CNSHA","VNSGN"),("KRPUS","JPYOK"),
        ("SGSIN","MYBTU"),("CNSHA","IDPNK"),
    ],
}

PORT_COORDS = {
    "CNSHA":(31.23,121.47),"NLRTM":(51.92,4.47),"DEHAM":(53.55,9.99),
    "USLAX":(33.74,-118.25),"USNYC":(40.65,-74.01),"SGSIN":(1.26,103.82),
    "KRPUS":(35.10,129.04),"TWKHH":(22.62,120.30),"GBFXT":(51.96,1.35),
    "BEANR":(51.23,4.40),"USLONG":(33.77,-118.22),"USSEA":(47.57,-122.34),
    "USOAK":(37.80,-122.27),"USSAV":(32.09,-81.10),"USBAL":(39.27,-76.58),
    "USBOS":(42.36,-71.05),"CNYTN":(22.65,114.26),"CNQIN":(36.07,120.33),
    "JPYOK":(35.44,139.64),"THLCH":(13.08,100.90),"PHMNL":(14.59,120.98),
    "VNSGN":(10.78,106.70),"INPAV":(18.96,72.94),"HKHKG":(22.32,114.19),
    "FRFOS":(43.29,5.38),"ESBCN":(41.35,2.17),"MYBTU":(5.84,118.11),
    "IDPNK":(-3.80,114.74),"GBFXT":(51.96,1.35),
}

VESSELS = [
    {"name":"Maersk Antares",        "imo":9302428,"op":"Maersk"},
    {"name":"MSC Gülsün",            "imo":9811000,"op":"MSC"},
    {"name":"OOCL Hong Kong",        "imo":9776171,"op":"OOCL"},
    {"name":"CMA CGM Jacques Saadé", "imo":9839430,"op":"CMA CGM"},
    {"name":"HMM Algeciras",         "imo":9863297,"op":"HMM"},
    {"name":"Ever Ace",              "imo":9831213,"op":"Evergreen"},
    {"name":"Hapag Hamburg",         "imo":9617027,"op":"Hapag-Lloyd"},
    {"name":"Yang Ming Witness",     "imo":9619250,"op":"Yang Ming"},
    {"name":"COSCO Universe",        "imo":9875431,"op":"COSCO"},
    {"name":"MSC Eloane",            "imo":9702183,"op":"MSC"},
    {"name":"ZIM Integrated",        "imo":9703291,"op":"ZIM"},
    {"name":"Maersk Kure",           "imo":9549727,"op":"Maersk"},
    {"name":"PIL Majestic",          "imo":9481033,"op":"PIL"},
    {"name":"APL Vanda",             "imo":9461814,"op":"APL"},
    {"name":"Wan Hai 307",           "imo":9381052,"op":"Wan Hai"},
    {"name":"COSCO Taurus",          "imo":9795611,"op":"COSCO"},
    {"name":"MSC Irina",             "imo":9930614,"op":"MSC"},
    {"name":"Ever Alot",             "imo":9943061,"op":"Evergreen"},
]

PREFIXES = [
    "MSKU","TCKU","CMAU","HLXU","GESU","PCIU","MSCU","APZU",
    "SUDU","NYKU","TRLU","UACU","FCIU","BMOU","OOLU","EITU",
    "TGHU","ZIMU","YMLU","COSU","MAEU","PONU","CLHU","WSKU",
    "SZLU","KKTU","HJSC","MEDU","FSCU","TCNU",
]

LEASE_TYPES   = ["One-Way","Master Lease","Long-Term"]
LEASE_WEIGHTS = [0.50, 0.30, 0.20]

# Initial fleet distribution by lifecycle stage
SEED_DIST = [
    ("GATE_IN",      0.12),
    ("AT_SEA",       0.38),
    ("ARRIVED",      0.05),
    ("IN_FREE_DAYS", 0.30),
    ("OVERDUE",      0.15),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cid(seed: str) -> str:
    prefix = PREFIXES[int(hashlib.md5(seed.encode()).hexdigest()[:2], 16) % len(PREFIXES)]
    num    = int(hashlib.md5(seed.encode()).hexdigest()[2:9], 16) % 9000000 + 1000000
    return f"{prefix}{num}"


def _lerp_pos(origin: str, dest: str, t: float) -> tuple:
    o = PORT_COORDS.get(origin, (0.0, 0.0))
    d = PORT_COORDS.get(dest,   (0.0, 0.0))
    lat = o[0] + (d[0] - o[0]) * t + random.uniform(-0.4, 0.4)
    lon = o[1] + (d[1] - o[1]) * t + random.uniform(-0.4, 0.4)
    return round(lat, 4), round(lon, 4)


# ── Spawn a new container seeded into a specific lifecycle stage ───────────────

def _spawn(seed: str, force_stage: str = None) -> dict:
    now  = datetime.now(timezone.utc)
    lane = random.choice(list(ROUTES.keys()))
    origin, dest = random.choice(ROUTES[lane])
    lease = random.choices(LEASE_TYPES, weights=LEASE_WEIGHTS)[0]
    free  = random.randint(*FREE_DAYS[lease])
    perd  = random.randint(*PER_DIEM[lease])
    v     = random.choice(VESSELS)
    trans = random.randint(*TRANSIT_HOURS[lane])
    gate  = random.randint(6, 36)

    if force_stage is None:
        stages  = [s for s, _ in SEED_DIST]
        weights = [w for _, w in SEED_DIST]
        force_stage = random.choices(stages, weights=weights)[0]

    # Set elapsed hours + LFD so container starts exactly at the right stage point
    if force_stage == "GATE_IN":
        # Brand new — just gated in, full voyage ahead
        elapsed_h      = random.uniform(0, gate * 0.7)
        stage_h        = elapsed_h
        lfd            = now + timedelta(hours=gate - elapsed_h + trans, days=free)
        lat, lon       = PORT_COORDS.get(origin, (0.0, 0.0))
        loc            = origin
        spd            = 0.0

    elif force_stage == "AT_SEA":
        # Mid-ocean: stage_h is how far through the transit leg
        stage_h  = random.uniform(trans * 0.05, trans * 0.90)
        elapsed_h = gate + stage_h
        progress  = stage_h / trans
        lfd       = now + timedelta(hours=trans - stage_h, days=free)
        lat, lon  = _lerp_pos(origin, dest, progress)
        loc       = f"At Sea ({v['name']})"
        spd       = round(random.uniform(14, 22), 1)

    elif force_stage == "ARRIVED":
        # Just discharged — still in port window
        stage_h   = random.uniform(0, 10)
        elapsed_h = gate + trans + stage_h
        lfd        = now + timedelta(days=free, hours=-stage_h * 0.5)
        lat, lon   = PORT_COORDS.get(dest, (0.0, 0.0))
        loc        = dest
        spd        = 0.0

    elif force_stage == "IN_FREE_DAYS":
        # Ashore, free days ticking down — buffer is positive (1 day → free_days-1 days)
        days_remaining = random.uniform(0.5, free - 0.5)
        stage_h   = 0
        elapsed_h = gate + trans + 12
        lfd       = now + timedelta(days=days_remaining)
        lat, lon  = PORT_COORDS.get(dest, (0.0, 0.0))
        loc       = dest
        spd       = 0.0

    elif force_stage == "OVERDUE":
        # Past LFD — penalty accumulating
        days_over = random.uniform(0.5, 14)
        stage_h   = 0
        elapsed_h = gate + trans + 12 + free * 24
        lfd       = now - timedelta(days=days_over)
        lat, lon  = PORT_COORDS.get(dest, (0.0, 0.0))
        loc       = dest
        spd       = 0.0
        perd      = random.randint(*PER_DIEM[lease])  # re-roll for variety

    else:
        elapsed_h = 0; stage_h = 0
        lfd = now + timedelta(days=free)
        lat, lon = PORT_COORDS.get(origin, (0.0, 0.0))
        loc = origin; spd = 0.0

    penalty = 0.0
    if force_stage == "OVERDUE":
        overdue_days = (now - lfd).total_seconds() / 86400
        penalty      = round(overdue_days * perd, 2)

    return {
        "container_id":       _cid(seed + origin + dest + lease),
        "contract_id":        f"CON{random.randint(100000,999999)}",
        "lease_type":         lease,
        "trade_lane":         lane,
        "vessel_name":        v["name"],
        "imo_number":         v["imo"],
        "operator":           v["op"],
        "origin_locode":      origin,
        "dest_locode":        dest,
        "free_days":          free,
        "per_diem_rate":      perd,
        "transit_hours":      trans,
        "gate_in_hours":      gate,
        "lfd_iso":            lfd.isoformat(),
        "lifecycle_stage":    force_stage,
        "stage_hours_elapsed":stage_h,
        "total_hours_elapsed":elapsed_h,
        "penalty_accrued_usd":penalty,
        "spawned_at":         now.isoformat(),
        "completed_at":       None,
        "current_lat":        lat,
        "current_lon":        lon,
        "current_loc":        loc,
        "speed_knots":        spd,
        "vessel_locked":      True,   # vessel_name/imo NEVER reassigned after birth
        "spawn_run":          None,   # set by tick_fleet() to the current run_count
    }


# ── Advance a container by elapsed hours ─────────────────────────────────────

def _advance(c: dict, hours: float) -> dict:
    """
    Advance container state by `hours` elapsed real time.

    Vessel retention rule (industrial practice):
      • GATE_IN / AT_SEA    → vessel active, show vessel_name + imo
      • ARRIVED / IN_FREE_DAYS / OVERDUE → vessel has berthed and departed.
        We retain vessel_name as "berthed_vessel" for historical reference
        but current_loc shows the port terminal, not the ship.

    Penalty rule (time-based, not recalculated from scratch each run):
      • New_Penalty = Previous_Penalty + (hours_elapsed / 24 * per_diem)
      • Triggered only when (LFD - NOW) < 0
    """
    c     = dict(c)
    now   = datetime.now(timezone.utc)
    lfd   = datetime.fromisoformat(c["lfd_iso"])
    stage = c["lifecycle_stage"]
    trans = c["transit_hours"]
    gate  = c["gate_in_hours"]
    origin= c["origin_locode"]
    dest  = c["dest_locode"]

    # Preserve vessel identity throughout — read once, never clear
    # Priority: vessel_name > berthed_vessel (set when vessel departed port)
    vessel_name = (c.get("vessel_name") or "").strip() or c.get("berthed_vessel", "")
    imo_number  = c.get("imo_number") or 0
    # Backfill berthed_vessel if vessel_name is known (ensures it's always set)
    if vessel_name and not c.get("berthed_vessel"):
        c["berthed_vessel"] = vessel_name

    c["last_updated"]        = now.isoformat()
    c["stage_hours_elapsed"] = c.get("stage_hours_elapsed", 0) + hours
    c["total_hours_elapsed"] = c.get("total_hours_elapsed", 0) + hours

    # ── GATE_IN ───────────────────────────────────────────────────────────────
    if stage == "GATE_IN":
        if c["stage_hours_elapsed"] >= gate:
            overshoot                = c["stage_hours_elapsed"] - gate
            c["lifecycle_stage"]     = "AT_SEA"
            c["stage_hours_elapsed"] = overshoot
            c["speed_knots"]         = round(random.uniform(14, 22), 1)
            stage = "AT_SEA"
        else:
            c["current_lat"], c["current_lon"] = PORT_COORDS.get(origin, (0.0, 0.0))
            c["current_loc"]  = f"Origin terminal — {origin}"
            c["speed_knots"]  = 0.0
            # Vessel assigned but not yet underway — show as "loading"
            c["vessel_name"]  = vessel_name
            c["imo_number"]   = imo_number

    # ── AT_SEA ────────────────────────────────────────────────────────────────
    if stage == "AT_SEA":
        progress = min(1.0, c["stage_hours_elapsed"] / max(trans, 1))
        lat, lon = _lerp_pos(origin, dest, progress)
        c["current_lat"]  = lat
        c["current_lon"]  = lon
        c["current_loc"]  = f"At Sea ({vessel_name})"
        c["speed_knots"]  = round(random.uniform(14, 22), 1)
        c["vessel_name"]  = vessel_name
        c["imo_number"]   = imo_number

        if c["stage_hours_elapsed"] >= trans:
            overshoot                = c["stage_hours_elapsed"] - trans
            c["lifecycle_stage"]     = "ARRIVED"
            c["stage_hours_elapsed"] = overshoot
            c["current_lat"], c["current_lon"] = PORT_COORDS.get(dest, (0.0, 0.0))
            c["current_loc"]  = f"Arrived — {dest}"
            c["speed_knots"]  = 0.0
            # Save the delivering vessel for historical reference
            c["berthed_vessel"] = vessel_name
            stage = "ARRIVED"

    # ── ARRIVED ───────────────────────────────────────────────────────────────
    if stage == "ARRIVED":
        c["current_loc"]  = f"Arrived — {dest}"
        c["speed_knots"]  = 0.0
        # FIX: retain vessel fields — do NOT null them. Container records must
        # show which vessel delivered it for audit and demurrage claim purposes.
        c["vessel_name"]  = vessel_name
        c["imo_number"]   = imo_number

        if c["stage_hours_elapsed"] >= 12:
            c["lifecycle_stage"]     = "IN_FREE_DAYS"
            c["stage_hours_elapsed"] = 0
            stage = "IN_FREE_DAYS"

    # ── IN_FREE_DAYS / OVERDUE ────────────────────────────────────────────────
    if stage in ("IN_FREE_DAYS", "OVERDUE"):
        c["current_loc"]  = f"Terminal — {dest}"
        c["speed_knots"]  = 0.0
        # FIX: retain vessel_name for audit trail even at terminal
        c["vessel_name"]  = vessel_name
        c["imo_number"]   = imo_number

        buffer_seconds = (lfd - now).total_seconds()

        if buffer_seconds <= 0:
            # TIME-BASED PENALTY: accumulate from previous run, not recalculate from scratch.
            # New_Penalty = Previous_Penalty + (hours_elapsed / 24 × per_diem_rate)
            # This prevents penalty "jumping" when run_count or sim_ts differs slightly.
            prev_penalty   = float(c.get("penalty_accrued_usd", 0) or 0)
            penalty_delta  = (hours / 24.0) * float(c["per_diem_rate"])
            c["penalty_accrued_usd"] = round(prev_penalty + penalty_delta, 2)
            c["lifecycle_stage"]     = "OVERDUE"

            # Deterministic case closure: once a container has been overdue
            # long enough to have received its 2-week escalation email plus
            # the grace period, treat it as resolved by the responsible
            # person and retire it — this keeps the fleet moving instead of
            # letting penalties accrue forever, and makes room for the next
            # batch of containers to enter (see tick_fleet's replenishment).
            overdue_days = abs(buffer_seconds) / 86400
            if overdue_days >= CLOSURE_AFTER_OVERDUE_DAYS:
                c["lifecycle_stage"] = "COMPLETE"
                c["completed_at"]    = now.isoformat()
                c["closure_reason"]  = (
                    f"Resolved after {overdue_days:.1f}d overdue "
                    f"({ESCALATION_THRESHOLD_DAYS}d escalation + {RESOLUTION_GRACE_DAYS}d grace)"
                )
        else:
            c["lifecycle_stage"]     = "IN_FREE_DAYS"
            c["penalty_accrued_usd"] = 0.0

    return c


# ── Ledger: primary (all containers, active + resolved) + final (resolved only) ─

def _load_ledger() -> dict:
    """Primary ledger — dict keyed by container_id, one record per container
    ever tracked, covering BOTH active and resolved containers. This is the
    single source of truth; the final ledger below is always derived from it."""
    if LEDGER_FILE.exists():
        try:
            return json.loads(LEDGER_FILE.read_text())
        except Exception as e:
            log.warning(f"Ledger corrupt, resetting: {e}")
    return {}


def _save_ledger(ledger: dict) -> None:
    LEDGER_FILE.write_text(json.dumps(ledger, indent=2, default=str))


def _upsert_ledger(ledger: dict, c: dict, status: str, now_iso: str) -> None:
    """Insert or update this container's record in the primary ledger.
    status is 'active' for every container still in the fleet, or
    'resolved' the moment it closes out. first_seen is preserved across
    updates; everything else reflects the container's latest known state."""
    cid = c.get("container_id")
    existing = ledger.get(cid, {})
    ledger[cid] = {
        "container_id":        cid,
        "status":               status,
        "trade_lane":           c.get("trade_lane"),
        "lease_type":           c.get("lease_type"),
        "vessel_name":          c.get("vessel_name"),
        "origin_locode":        c.get("origin_locode"),
        "dest_locode":          c.get("dest_locode"),
        "per_diem_rate":        c.get("per_diem_rate"),
        "lifecycle_stage":      c.get("lifecycle_stage"),
        "penalty_accrued_usd":  c.get("penalty_accrued_usd", 0),
        "first_seen":           existing.get("first_seen", now_iso),
        "last_updated":         now_iso,
        "completed_at":         c.get("completed_at")   if status == "resolved" else existing.get("completed_at"),
        "closure_reason":       c.get("closure_reason")  if status == "resolved" else existing.get("closure_reason"),
    }


def _export_final_ledger(ledger: dict) -> None:
    """
    The final ledger is not written independently — it's fetched from the
    primary ledger by filtering to status == 'resolved'. This guarantees
    the two files can never drift out of sync with each other; the primary
    ledger is always the source of truth.
    """
    resolved = [rec for rec in ledger.values() if rec.get("status") == "resolved"]
    resolved.sort(key=lambda r: r.get("completed_at") or "")
    if len(resolved) > MAX_CLOSURE_LOG_ENTRIES:
        resolved = resolved[-MAX_CLOSURE_LOG_ENTRIES:]
    CLOSURE_LOG_FILE.write_text(json.dumps(resolved, indent=2, default=str))


# ── Load / Save ───────────────────────────────────────────────────────────────

def _load() -> dict:
    empty = {"containers": [], "last_run_iso": None, "run_count": 0}
    if STATE_FILE.exists():
        try:
            raw = STATE_FILE.read_text().strip()
            if not raw:
                return empty
            parsed = json.loads(raw)
            # Must be a dict — reject [], null, etc
            if not isinstance(parsed, dict):
                log.warning("State file is not a dict, resetting")
                return empty
            return parsed
        except Exception as e:
            log.warning(f"State corrupt, resetting: {e}")
    return empty


def _save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _log_final_snapshot(c: dict, run_number: int) -> None:
    """Write one last dashboard-visible history entry before a closed
    container disappears from the active fleet, so its journey view ends
    with 'Resolved' instead of just stopping without explanation."""
    from pipeline.history_tracker import append_closure_snapshot
    now = datetime.now(timezone.utc)
    snap = {
        "ts":     now.isoformat()[:19],
        "run":    run_number,
        "stage":  "COMPLETE",
        "lat":    round(float(c.get("current_lat", 0) or 0), 3),
        "lon":    round(float(c.get("current_lon", 0) or 0), 3),
        "dtl":    0.0,
        "status": "Resolved",
        "burn":   round(float(c.get("penalty_accrued_usd", 0) or 0), 1),
        "pct":    100,
        "vessel": str(c.get("vessel_name", ""))[:30],
        "loc":    ("Case closed — " + str(c.get("closure_reason", "")))[:60],
    }
    append_closure_snapshot(c.get("container_id"), snap)


# ── Public API ────────────────────────────────────────────────────────────────

def days_to_lfd(c: dict) -> float:
    now = datetime.now(timezone.utc)
    lfd = datetime.fromisoformat(c["lfd_iso"])
    return round((lfd - now).total_seconds() / 86400, 2)


def tick_fleet() -> list:
    """
    Advance the full fleet by elapsed real time.
    Returns the current live fleet (COMPLETE containers excluded).
    """
    now   = datetime.now(timezone.utc)
    state = _load()

    # Elapsed since last run (hours) — use .get() to handle empty/partial state
    last_run = state.get("last_run_iso")
    if last_run:
        try:
            last    = datetime.fromisoformat(last_run)
            elapsed = max(0.25, (now - last).total_seconds() / 3600)
        except Exception:
            elapsed = 1.0
    else:
        elapsed = 1.0

    log.info(f"Fleet tick: {elapsed:.2f}h elapsed since last run")

    containers = state.get("containers") or []

    # Seed fresh fleet if empty
    if not containers:
        log.info(f"Seeding fresh fleet of {FLEET_SIZE} containers...")
        stages  = [s for s, _ in SEED_DIST]
        weights = [w for _, w in SEED_DIST]
        for i in range(FLEET_SIZE):
            fs = random.choices(stages, weights=weights)[0]
            c_new = _spawn(seed=f"seed_{i}_{now.timestamp()}", force_stage=fs)
            c_new["spawn_run"] = 1   # first run
            containers.append(c_new)

    # Advance all non-COMPLETE containers
    ledger    = _load_ledger()
    now_iso   = now.isoformat()
    active    = []
    completed = 0
    for c in containers:
        if c.get("lifecycle_stage") == "COMPLETE":
            completed += 1
            continue
        advanced = _advance(c, elapsed)
        if advanced["lifecycle_stage"] == "COMPLETE":
            completed += 1
            _upsert_ledger(ledger, advanced, "resolved", now_iso)
            _log_final_snapshot(advanced, state.get("run_count", 0) + 1)
            log.info(
                f"  ✓ Closed {advanced['container_id']} — "
                f"${advanced.get('penalty_accrued_usd', 0):,.2f} accrued — "
                f"logged to ledger, replacing with next batch"
            )
        else:
            active.append(advanced)

    log.info(f"Advanced {len(active)} containers | {completed} completed this tick")

    # Replace completed + fill fleet back to target
    shortage = FLEET_SIZE - len(active)
    for i in range(shortage):
        new_c = _spawn(seed=f"new_{i}_{now.timestamp()}_{random.random()}")
        new_c["spawn_run"] = state.get("run_count", 0) + 1  # +1 because run_count increments after
        active.append(new_c)
        log.info(f"  + Spawned {new_c['container_id']} ({new_c['trade_lane']} | {new_c['lease_type']} | stage={new_c['lifecycle_stage']})")

    # Primary ledger: every currently-active container gets upserted with
    # status="active" — the resolved entries above were already written in
    # the same pass, so this single ledger now holds both, and the final
    # ledger (completed_containers.json) is fetched from it below.
    for c in active:
        _upsert_ledger(ledger, c, "active", now_iso)
    _save_ledger(ledger)
    _export_final_ledger(ledger)

    state["containers"]   = active
    state["last_run_iso"] = now.isoformat()
    state["run_count"]    = int(state.get("run_count") or 0) + 1
    _save(state)

    from collections import Counter
    stages = Counter(c["lifecycle_stage"] for c in active)
    log.info("Fleet: " + " | ".join(f"{s}={n}" for s, n in sorted(stages.items())))
    return active
