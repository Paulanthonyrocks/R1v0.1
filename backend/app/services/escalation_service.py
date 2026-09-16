"""Escalation + ack workflow (feature 2).

Pure time-based escalation state; no SMS/siren hardware here.
Levels and timeout come from config `escalation:`; notification channels
reuse NotificationService webhooks. IncidentManager owns ack timestamps;
this service only computes DUE/OVERDUE so routers can surface SLA.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.services.escalation")

OK = "OK"
DUE = "DUE"
OVERDUE = "OVERDUE"
ACKED = "ACKED"


def escalation_state(
    created_ts: float,
    ack_ts: Optional[float],
    now_ts: float,
    ack_timeout_sec: float = 300.0,
    overdue_multiplier: float = 3.0,
) -> str:
    if ack_ts is not None:
        return ACKED
    elapsed = now_ts - created_ts
    if elapsed >= ack_timeout_sec * overdue_multiplier:
        return OVERDUE
    if elapsed >= ack_timeout_sec:
        return DUE
    return OK


def next_level(elapsed_sec: float, levels: List[Dict[str, Any]]) -> Optional[str]:
    """levels: [{name, after_sec}...] sorted ascending. Returns highest due level."""
    due = None
    for lvl in sorted(levels, key=lambda l: l.get("after_sec", 0)):
        if elapsed_sec >= lvl.get("after_sec", 0):
            due = lvl.get("name")
    return due


class EscalationService:
    def __init__(self, config: Dict[str, Any]):
        cfg = config.get("escalation", {})
        self.enabled = cfg.get("enabled", True)
        self.ack_timeout_sec = float(cfg.get("ack_timeout_sec", 300))
        self.overdue_multiplier = float(cfg.get("overdue_multiplier", 3.0))
        self.levels: List[Dict[str, Any]] = cfg.get("levels", [
            {"name": "operator", "after_sec": 0},
            {"name": "supervisor", "after_sec": 300},
            {"name": "command", "after_sec": 900},
        ])

    def state(self, created_ts: float, ack_ts: Optional[float], now_ts: float) -> str:
        return escalation_state(
            created_ts, ack_ts, now_ts, self.ack_timeout_sec, self.overdue_multiplier
        )

    def level(self, created_ts: float, now_ts: float) -> Optional[str]:
        return next_level(now_ts - created_ts, self.levels)
