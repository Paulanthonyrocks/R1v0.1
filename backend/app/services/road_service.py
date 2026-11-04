import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.roads import Road, RoadCreate, RoadUpdate
from app.utils import geo_utils

logger = logging.getLogger("app.services.road_service")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\-]+", "-", name.strip().lower()).strip("-")
    return slug or "road"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RoadService:
    """Registry of roads/corridors, persisted as JSON (same pattern as
    FeedRegistry). Roads are the graph edges; intersection node ids on a road
    are the vertices the node-congestion registry already knows."""

    def __init__(self, persistence_path):
        self.persistence_path = Path(persistence_path)
        self._lock = asyncio.Lock()
        self._roads: Dict[str, Road] = {}
        self._load()

    def _load(self) -> None:
        if not self.persistence_path.exists():
            return
        try:
            with open(self.persistence_path, "r") as f:
                data = json.load(f)
            for rid, rdata in data.items():
                try:
                    self._roads[rid] = Road(**rdata)
                except Exception as e:
                    logger.error(f"Failed to load road {rid}: {e}")
            logger.info(f"Loaded {len(self._roads)} roads from {self.persistence_path}")
        except Exception as e:
            logger.error(f"Failed to load roads persistence: {e}")

    def _save(self) -> None:
        try:
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {rid: r.model_dump(mode="json") for rid, r in self._roads.items()}
            tmp = self.persistence_path.with_suffix(self.persistence_path.suffix + ".tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self.persistence_path)
        except Exception as e:
            logger.error(f"Failed to save roads persistence: {e}")

    async def list_roads(self) -> List[Road]:
        async with self._lock:
            return sorted(self._roads.values(), key=lambda r: r.name.lower())

    async def get_road(self, road_id: str) -> Optional[Road]:
        async with self._lock:
            return self._roads.get(road_id)

    async def create_road(self, req: RoadCreate) -> Road:
        async with self._lock:
            base = _slugify(req.name)
            rid = base
            n = 2
            while rid in self._roads:
                rid = f"{base}-{n}"
                n += 1
            now = _utcnow()
            road = Road(
                id=rid, name=req.name, polyline=req.polyline,
                speed_limit_kmh=req.speed_limit_kmh,
                one_way_bearing_deg=req.one_way_bearing_deg,
                intersection_ids=list(req.intersection_ids),
                created_at=now, updated_at=now,
            )
            self._roads[rid] = road
            self._save()
            return road

    async def update_road(self, road_id: str, req: RoadUpdate) -> Optional[Road]:
        async with self._lock:
            road = self._roads.get(road_id)
            if not road:
                return None
            updated = road.model_copy(
                update={**req.model_dump(exclude_unset=True), "updated_at": _utcnow()}
            )
            self._roads[road_id] = updated
            self._save()
            return updated

    async def delete_road(self, road_id: str) -> bool:
        async with self._lock:
            if road_id not in self._roads:
                return False
            del self._roads[road_id]
            self._save()
            return True

    def snap(self, latitude: float, longitude: float, max_distance_m: float = 150.0) -> Dict[str, Any]:
        """Snap a point to the nearest road (sync, read-only). Returns a
        RoadSnap-shaped dict."""
        return geo_utils.snap_to_roads(latitude, longitude, list(self._roads.values()), max_distance_m)

    def describe_snap(
        self,
        latitude: float,
        longitude: float,
        road_id: Optional[str] = None,
        max_distance_m: float = 150.0,
    ) -> Dict[str, Any]:
        """
        Snap to one road (road_id given) or the nearest of all roads, and
        add corridor context: chainage along the road, segment bearing, and
        the nearest registered intersection (intersection_ids are "lat,lng"
        node ids). Used by incident language (incident_language.py).
        """
        if road_id:
            road = self._roads.get(road_id)
            roads = [road] if road else []
        else:
            roads = list(self._roads.values())
        snap = geo_utils.snap_to_roads(latitude, longitude, roads, max_distance_m)
        if not snap.get("found"):
            return {"found": False}
        # snap_to_roads flattens road fields; recover the Road model for
        # intersection_ids.
        road_model = self._roads.get(snap.get("road_id") or "")
        nearest_inter = None
        nearest_d = None
        if road_model is not None:
            for iid in road_model.intersection_ids or []:
                try:
                    la, ln = (float(x) for x in str(iid).split(","))
                except (ValueError, TypeError):
                    continue
                d = geo_utils.haversine_m(latitude, longitude, la, ln)
                if nearest_d is None or d < nearest_d:
                    nearest_inter, nearest_d = iid, d
        return {
            "found": True,
            "road_id": snap.get("road_id"),
            "road_name": snap.get("road_name"),
            "point": snap.get("point"),
            "distance_m": snap.get("distance_m"),
            "distance_along_m": snap.get("distance_along_m"),
            "segment_bearing_deg": snap.get("segment_bearing_deg"),
            "nearest_intersection_id": nearest_inter,
            "distance_to_intersection_m": nearest_d,
        }


_instance: Optional[RoadService] = None


def get_road_service(config: Optional[Dict[str, Any]] = None) -> RoadService:
    """Process-wide singleton, lazily created from config (roads_config_path,
    default backend/data/roads.json, same CWD-relative convention as
    feeds_config_path)."""
    global _instance
    if _instance is None:
        path = (config or {}).get("roads_config_path", "backend/data/roads.json")
        _instance = RoadService(path)
    return _instance
