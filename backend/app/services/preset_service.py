import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.presets import Preset, PresetCreate, PresetUpdate

logger = logging.getLogger("app.services.preset_service")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\-]+", "-", name.strip().lower()).strip("-")
    return slug or "preset"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PresetService:
    """Warden presets (named camera groups), persisted as JSON — same
    pattern as RoadService/FeedRegistry."""

    def __init__(self, persistence_path):
        self.persistence_path = Path(persistence_path)
        self._lock = asyncio.Lock()
        self._presets: Dict[str, Preset] = {}
        self._load()

    def _load(self) -> None:
        if not self.persistence_path.exists():
            return
        try:
            with open(self.persistence_path, "r") as f:
                data = json.load(f)
            for pid, pdata in data.items():
                try:
                    self._presets[pid] = Preset(**pdata)
                except Exception as e:
                    logger.error(f"Failed to load preset {pid}: {e}")
            logger.info(f"Loaded {len(self._presets)} presets from {self.persistence_path}")
        except Exception as e:
            logger.error(f"Failed to load presets persistence: {e}")

    def _save(self) -> None:
        try:
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {pid: p.model_dump(mode="json") for pid, p in self._presets.items()}
            tmp = self.persistence_path.with_suffix(self.persistence_path.suffix + ".tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self.persistence_path)
        except Exception as e:
            logger.error(f"Failed to save presets persistence: {e}")

    async def list_presets(self) -> List[Preset]:
        async with self._lock:
            return sorted(self._presets.values(), key=lambda p: p.name.lower())

    async def get_preset(self, preset_id: str) -> Optional[Preset]:
        async with self._lock:
            return self._presets.get(preset_id)

    async def create_preset(self, req: PresetCreate) -> Preset:
        async with self._lock:
            base = _slugify(req.name)
            pid = base
            n = 2
            while pid in self._presets:
                pid = f"{base}-{n}"
                n += 1
            now = _utcnow()
            preset = Preset(
                id=pid, name=req.name,
                feed_ids=list(dict.fromkeys(req.feed_ids)),  # dedupe, keep order
                road_id=req.road_id,
                created_at=now, updated_at=now,
            )
            self._presets[pid] = preset
            self._save()
            return preset

    async def update_preset(self, preset_id: str, req: PresetUpdate) -> Optional[Preset]:
        async with self._lock:
            preset = self._presets.get(preset_id)
            if not preset:
                return None
            updates = req.model_dump(exclude_unset=True)
            if "feed_ids" in updates and updates["feed_ids"] is not None:
                updates["feed_ids"] = list(dict.fromkeys(updates["feed_ids"]))
            updated = preset.model_copy(update={**updates, "updated_at": _utcnow()})
            self._presets[preset_id] = updated
            self._save()
            return updated

    async def delete_preset(self, preset_id: str) -> bool:
        async with self._lock:
            if preset_id not in self._presets:
                return False
            del self._presets[preset_id]
            self._save()
            return True


_instance: Optional[PresetService] = None


def get_preset_service(config: Optional[Dict[str, Any]] = None) -> PresetService:
    """Process-wide singleton, lazily created from config (presets_config_path,
    default backend/data/presets.json, same CWD-relative convention as
    feeds_config_path / roads_config_path)."""
    global _instance
    if _instance is None:
        path = (config or {}).get("presets_config_path", "backend/data/presets.json")
        _instance = PresetService(path)
    return _instance
