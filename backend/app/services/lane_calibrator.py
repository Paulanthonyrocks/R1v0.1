import numpy as np
import math
import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger("app.services.lane_calibrator")

class LaneCalibrator:
    """
    Autonomously learns the dominant traffic flow direction for each lane.
    Uses a running consensus of normalized velocity vectors.
    """
    
    def __init__(self, min_samples: int = 20, max_samples: int = 100, confidence_threshold: float = 0.8,
                 two_way_opposed_fraction: float = 0.03, two_way_min_samples: int = 40,
                 two_way_sustain_calls: int = 400):
        self.min_samples = min_samples
        self.max_samples = max_samples
        self.confidence_threshold = confidence_threshold
        # Two-way verdict: a lane whose samples sustainably oppose the consensus
        # is bidirectional (or straddles two carriageways), so "wrong-way" is
        # meaningless there. Legs: enough samples, a non-trivial opposed share
        # of the CURRENT window (so a resolved distribution clears the verdict),
        # and that share sustained over enough consecutive add_sample calls to
        # prove a real stream. The sustain leg is what separates an oncoming
        # stream from a 2-second lane-changer: calls arrive per vehicle per
        # frame (~25/sec/lane at 5fps x 5 vehicles), so a brief event
        # contributes ~100 calls and never trips 400, while a live stream
        # trips it in ~15s. The streak DECAYS (not resets) when the window
        # fraction dips under the bar, so a flickering imbalanced stream still
        # accumulates while a resolved one drains back to zero in seconds.
        self.two_way_opposed_fraction = two_way_opposed_fraction
        self.two_way_min_samples = two_way_min_samples
        self.two_way_sustain_calls = two_way_sustain_calls
        
        # { feed_id: { lane_id: { "vectors": [], "consensus": list, "confidence": float } } }
        self.lane_data: Dict[str, Dict[int, Dict]] = {}

    def add_sample(self, feed_id: str, lane_id: int, vx: float, vy: float):
        """Adds a velocity sample to the lane's calibration buffer."""
        if feed_id not in self.lane_data:
            self.lane_data[feed_id] = {}
        
        if lane_id not in self.lane_data[feed_id]:
            self.lane_data[feed_id][lane_id] = {
                "vectors": [],
                "consensus": None,
                "confidence": 0.0
            }
        
        # Normalize sample vector
        mag = math.sqrt(vx*vx + vy*vy)
        if mag < 0.5: # Ignore vehicles that are nearly stationary
            return
            
        norm_v = (vx / mag, vy / mag)
        data = self.lane_data[feed_id][lane_id]
        
        data["vectors"].append(norm_v)
        
        # Maintain rolling window
        if len(data["vectors"]) > self.max_samples:
            data["vectors"].pop(0)
            
        # Update calibration if we have enough samples
        if len(data["vectors"]) >= self.min_samples:
            self._update_consensus(feed_id, lane_id)

    def _update_consensus(self, feed_id: str, lane_id: int):
        """Calculates the mean vector and alignment confidence."""
        data = self.lane_data[feed_id][lane_id]
        vectors = data["vectors"]
        
        # Average normalized vectors
        avg_x = sum(v[0] for v in vectors) / len(vectors)
        avg_y = sum(v[1] for v in vectors) / len(vectors)
        
        # Magnitude of the average vector indicates alignment (1.0 = perfect, 0.0 = random)
        alignment = math.sqrt(avg_x**2 + avg_y**2)
        
        if alignment < 0.1:
            # Random directions, no consensus (and no two-way verdict either --
            # leave no stale flag from an earlier distribution).
            data["consensus"] = None
            data["confidence"] = 0.0
            data["opposed_fraction"] = 0.0
            data["two_way"] = False
            return
            
        # Normalize consensus vector
        data["consensus"] = [avg_x / alignment, avg_y / alignment]

        # Opposed fraction: share of samples pointing against the consensus.
        # A real direction-of-travel minority (e.g. a lighter oncoming stream
        # sharing the band) holds this over the bar across thousands of calls;
        # a lane-changer clears it in ~2s. NOTE (Sep-04 live): imbalanced
        # streams (5% opposed, conf 0.9+) still flag the whole minority, so
        # the fraction bar sits low (0.03) and the sustain leg proves the
        # stream. Do NOT accumulate opposed counts across recomputes: the
        # recompute runs per sample, so a 10-sample lane-change would inflate
        # past any absolute tripwire while sitting in the rolling window.
        cx, cy = data["consensus"]
        opposed = sum(1 for v in vectors if v[0] * cx + v[1] * cy < 0)
        data["opposed_fraction"] = opposed / len(vectors)
        # Fast-attack / bounded-release accumulator (Sep-05 live: symmetric
        # +/-1 let streaks balloon to 31000, blinding a tripped lane for
        # minutes after the evidence cleared, while hover-at-bar lanes took
        # 28 min to trip). Over-bar steps +2 so a solid stream trips in
        # ~200 calls (~8s) and a hover (51% over) in ~750 (~30s); a
        # 100-call lane-change burst reaches only ~+200 < 400. The cap
        # (2x sustain) bounds post-clear blindness to ~400 calls (~15-30s).
        # Under-bar still decays -1/call, so a resolved lane clears fully.
        _streak_cap = self.two_way_sustain_calls * 2
        if data["opposed_fraction"] >= self.two_way_opposed_fraction:
            data["over_streak"] = min(_streak_cap, data.get("over_streak", 0) + 2)
        else:
            # Decay, not reset: a flickering stream (over, under, over)
            # keeps accumulating toward the verdict, while a truly resolved
            # distribution drains to zero within seconds. A hard reset let a
            # noisy imbalanced band (Sep-05: 147 flags, 4 skips) never trip.
            data["over_streak"] = max(0, data.get("over_streak", 0) - 1)
        data["two_way"] = (
            len(vectors) >= self.two_way_min_samples
            and data["over_streak"] >= self.two_way_sustain_calls
        )
        
        # Confidence score scales with alignment and sample count
        sample_multiplier = min(1.0, len(vectors) / self.min_samples)
        data["confidence"] = alignment * sample_multiplier
        
        if data["confidence"] >= self.confidence_threshold and len(vectors) == self.min_samples:
            logger.info(f"Lane Calibration Complete for feed {feed_id}, lane {lane_id}: {data['consensus']} (Conf: {data['confidence']:.2f})")

    def get_flow_vector(self, feed_id: str, lane_id: int) -> Tuple[Optional[List[float]], float]:
        """Returns the consensus flow vector and its confidence score."""
        lane_map = self.lane_data.get(feed_id)
        if not lane_map:
            return None, 0.0
            
        data = lane_map.get(lane_id)
        if not data or not data["consensus"]:
            # Fallback: if specific lane unknown, try 'all lanes' (-1) if it has high confidence
            if lane_id != -1 and -1 in lane_map:
                fallback = lane_map[-1]
                if fallback["confidence"] > 0.9:
                    return fallback["consensus"], fallback["confidence"]
            return None, 0.0
            
        return data["consensus"], data["confidence"]

    def is_two_way(self, feed_id: str, lane_id: int) -> bool:
        """True when the lane's samples materially oppose its own consensus.

        Mirrors get_flow_vector's lane=-1 fallback so the verdict covers the
        same consensus SafetyMonitor would judge against.
        """
        lane_map = self.lane_data.get(feed_id)
        if not lane_map:
            return False
        data = lane_map.get(lane_id)
        if (not data or not data["consensus"]) and lane_id != -1 and -1 in lane_map:
            fallback = lane_map[-1]
            if fallback["confidence"] > 0.9:
                data = fallback
        if not data or not data["consensus"]:
            return False
        return bool(data.get("two_way", False))

    def is_calibrated(self, feed_id: str, lane_id: int) -> bool:
        """Returns True if the lane has reached the calibration confidence threshold."""
        _, confidence = self.get_flow_vector(feed_id, lane_id)
        return confidence >= self.confidence_threshold

    def get_lane_stats(self, feed_id: str, lane_id: int) -> Dict:
        """Per-lane two-way diagnostics for the flow-vector log line.

        Lets the next wrong-way storm stay attributable: opposed_fraction /
        over_streak show whether the verdict is approaching or the band is
        clean and the flag is a genuine opposition.
        """
        lane_map = self.lane_data.get(feed_id, {})
        data = lane_map.get(lane_id, {})
        return {
            "samples": len(data.get("vectors", [])),
            "opposed_fraction": round(data.get("opposed_fraction", 0.0), 3),
            "over_streak": int(data.get("over_streak", 0)),
            "two_way": bool(data.get("two_way", False)),
        }

    def get_calibration_status(self, feed_id: str) -> Dict:
        """Returns a summary of calibration status for a feed."""
        if feed_id not in self.lane_data:
            return {}
            
        return {
            lane_id: {
                "calibrated": data["confidence"] >= self.confidence_threshold,
                "confidence": round(data["confidence"], 2),
                "samples": len(data["vectors"]),
                "two_way": bool(data.get("two_way", False)),
                "opposed_fraction": round(data.get("opposed_fraction", 0.0), 3),
                "over_streak": int(data.get("over_streak", 0)),
            }
            for lane_id, data in self.lane_data[feed_id].items()
        }
