"""ANPR allow/block list (feature 8).

Honest stub: no plate recognizer is bundled (needs legible plates + a
model). When `anpr.enabled` is false, check_plate reports unknown and
list endpoints return []. When enabled with static lists, matching is a
pure string lookup. Wire a real OCR provider later behind the same API.
"""
import logging
from typing import Any, Dict, List

logger = logging.getLogger("app.services.anpr")


def normalize_plate(plate: str) -> str:
    return "".join(c for c in str(plate).upper() if c.isalnum())


def check_plate_lists(plate: str, allow: List[str], block: List[str]) -> Dict[str, Any]:
    norm = normalize_plate(plate)
    allow_n = {normalize_plate(p) for p in allow}
    block_n = {normalize_plate(p) for p in block}
    return {
        "plate": norm,
        "blocked": norm in block_n,
        "allowed": norm in allow_n,
        "known": norm in allow_n or norm in block_n,
    }


class ANPRService:
    def __init__(self, config: Dict[str, Any]):
        cfg = config.get("anpr", {})
        self.enabled = cfg.get("enabled", False)
        self.allow_list: List[str] = cfg.get("allow_list", [])
        self.block_list: List[str] = cfg.get("block_list", [])
        if not self.enabled:
            logger.info("ANPRService disabled (no recognizer configured).")

    def check_plate(self, plate: str) -> Dict[str, Any]:
        if not self.enabled:
            return {"plate": normalize_plate(plate), "blocked": False,
                    "allowed": False, "known": False, "unconfigured": True}
        result = check_plate_lists(plate, self.allow_list, self.block_list)
        result["unconfigured"] = False
        return result

    def lists(self) -> Dict[str, List[str]]:
        if not self.enabled:
            return {"allow": [], "block": []}
        return {"allow": list(self.allow_list), "block": list(self.block_list)}
