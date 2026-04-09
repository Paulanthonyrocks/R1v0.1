import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
import numpy as np
from dataclasses import dataclass
import networkx as nx
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RouteSegment:
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    distance_km: float
    typical_duration_mins: float
    predicted_duration_mins: float
    congestion_score: float
    confidence: float


@dataclass
class OptimizedRoute:
    segments: List[RouteSegment]
    total_distance_km: float
    estimated_duration_mins: float
    confidence_score: float
    alternative_routes: List[List[RouteSegment]]
    congestion_probability: float
    recommendations: List[str]


class DataCache:
    def get_statistics(self, latitude, longitude, hours):
        return {"average_speed": 50.0, "typical_congestion": 0.2}


class RouteOptimizer:
    def __init__(self, traffic_predictor, data_cache):
        self.road_graph = nx.DiGraph()
        self._initialize_road_graph()
        self.traffic_predictor = traffic_predictor
        self.data_cache = data_cache

    def _initialize_road_graph(self):
        self._create_sample_grid_network()

    def _create_sample_grid_network(self):
        for i in range(5):
            for j in range(5):
                self.road_graph.add_node(
                    f"{i},{j}", lat=34.0 + i * 0.01, lon=-118.0 + j * 0.01
                )
        for i in range(5):
            for j in range(5):
                if i < 4:
                    self.road_graph.add_edge(
                        f"{i},{j}", f"{i + 1},{j}", weight=1.0, distance_km=1.0
                    )
                if j < 4:
                    self.road_graph.add_edge(
                        f"{i},{j}", f"{i},{j + 1}", weight=1.0, distance_km=1.0
                    )

    def predict_segment_conditions(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        prediction_time: datetime,
    ) -> Dict[str, Any]:
        input_data = {
            "sensor_id": "dummy_sensor",
            "timestamp": prediction_time,
            "latitude": (start_lat + end_lat) / 2,
            "longitude": (start_lon + end_lon) / 2,
            "vehicle_count": 50,
            "average_speed": 40.0,
            "congestion_level": 3.0,
            "congestion_score": 50.0,
            "processing_timestamp": datetime.now(timezone.utc),
            "status": "simulated",
            "hour_of_day": prediction_time.hour,
            "day_of_week": prediction_time.weekday(),
            "is_weekend": prediction_time.weekday() >= 5,
            "road_type": "major_artery",
            "weather_conditions_temperature": 20.0,
            "weather_conditions_precipitation": 0.0,
            "truck_percentage": 0.15,
            "is_outlier": False,
            "incident_occurred": 0,
        }
        input_df = pd.DataFrame([input_data])
        prediction = self.traffic_predictor.predict_incident_likelihood(input_df)
        base_duration = self._calculate_base_duration(
            start_lat, start_lon, end_lat, end_lon
        )
        congestion_factor = 1.0 + (prediction["incident_likelihood"] * 2)
        predicted_duration = base_duration * congestion_factor
        typical_conditions = self.data_cache.get_statistics(
            latitude=(start_lat + end_lat) / 2,
            longitude=(start_lon + end_lon) / 2,
            hours=1,
        )
        return {
            "predicted_duration_mins": predicted_duration,
            "congestion_score": prediction["incident_likelihood"],
            "confidence": prediction.get("confidence_score", 0.7),
            "typical_conditions": typical_conditions,
        }

    def _calculate_base_duration(
        self, start_lat: float, start_lon: float, end_lat: float, end_lon: float
    ) -> float:
        distance = self._haversine_distance(start_lat, start_lon, end_lat, end_lon)
        return (distance / 60.0) * 60

    def _haversine_distance(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        R = 6371
        lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(np.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad
        a = (
            np.sin(dlat / 2) ** 2
            + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2) ** 2
        )
        c = 2 * np.arcsin(np.sqrt(a))
        return R * c

    def optimize_route(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        departure_time: datetime,
        consider_alternatives: bool = True,
    ) -> OptimizedRoute:
        """Find the optimal route considering predicted traffic conditions"""
        try:
            self._update_graph_weights(departure_time)
            path, start_node, end_node = self._find_optimal_path(start_lat, start_lon, end_lat, end_lon)
            if not path or not start_node or not end_node:
                raise ValueError("Could not compute optimal path.")
            
            route_segments = self._create_route_segments(path, departure_time)
            total_distance = sum(seg.distance_km for seg in route_segments)
            total_duration = sum(seg.predicted_duration_mins for seg in route_segments)
            avg_confidence = np.mean([seg.confidence for seg in route_segments])
            
            alternatives = []
            if consider_alternatives:
                alternatives = self._find_alternative_routes(
                    start_node, end_node, path, departure_time
                )

            recommendations = self._generate_route_recommendations(
                route_segments, alternatives, departure_time
            )

            return OptimizedRoute(
                segments=route_segments,
                total_distance_km=total_distance,
                estimated_duration_mins=total_duration,
                confidence_score=avg_confidence,
                alternative_routes=alternatives,
                congestion_probability=self._calculate_congestion_probability(
                    route_segments
                ),
                recommendations=recommendations,
            )

        except Exception as e:
            logger.error(f"Error optimizing route: {e}")
            raise

    def _update_graph_weights(self, prediction_time: datetime):
        for u, v, data in self.road_graph.edges(data=True):
            node_u, node_v = self.road_graph.nodes[u], self.road_graph.nodes[v]
            conditions = self.predict_segment_conditions(
                node_u["lat"], node_u["lon"], node_v["lat"], node_v["lon"], prediction_time
            )
            self.road_graph[u][v]["weight"] = conditions["predicted_duration_mins"]
            self.road_graph[u][v]["congestion_score"] = conditions["congestion_score"]

    def _find_optimal_path(
        self, start_lat: float, start_lon: float, end_lat: float, end_lon: float
    ) -> Tuple[Optional[List[str]], Optional[str], Optional[str]]:
        """Find the optimal path in the graph using A* search."""
        start_node = self._find_nearest_node(start_lat, start_lon)
        end_node = self._find_nearest_node(end_lat, end_lon)
        
        if not start_node or not end_node:
            raise ValueError("Could not find nearest nodes for start/end points.")

        end_node_data = self.road_graph.nodes[end_node]
        
        def heuristic(u, v):
            node_u_data = self.road_graph.nodes[u]
            dist = self._haversine_distance(node_u_data["lat"], node_u_data["lon"], end_node_data["lat"], end_node_data["lon"])
            return dist / 2.16 # Admissible heuristic assuming max speed of 130km/h

        try:
            path = nx.astar_path(
                self.road_graph, start_node, end_node, heuristic=heuristic, weight="weight"
            )
            return path, start_node, end_node
        except nx.NetworkXNoPath:
            raise ValueError("No route found between the specified points")

    def _find_nearest_node(self, lat: float, lon: float) -> str:
        return min(
            self.road_graph.nodes,
            key=lambda node: self._haversine_distance(
                lat, lon, self.road_graph.nodes[node]["lat"], self.road_graph.nodes[node]["lon"]
            ),
        )

    def _create_route_segments(
        self, path: List[str], departure_time: datetime
    ) -> List[RouteSegment]:
        segments = []
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            node_u, node_v = self.road_graph.nodes[u], self.road_graph.nodes[v]
            edge_data = self.road_graph[u][v]
            conditions = self.predict_segment_conditions(
                node_u["lat"], node_u["lon"], node_v["lat"], node_v["lon"],
                departure_time + timedelta(minutes=sum(seg.predicted_duration_mins for seg in segments)),
            )
            segments.append(RouteSegment(
                start_lat=node_u["lat"], start_lon=node_u["lon"], end_lat=node_v["lat"], end_lon=node_v["lon"],
                distance_km=edge_data["distance_km"], typical_duration_mins=(edge_data["distance_km"] * 60 / 60),
                predicted_duration_mins=conditions["predicted_duration_mins"], congestion_score=conditions["congestion_score"],
                confidence=conditions["confidence"],
            ))
        return segments

    def _find_alternative_routes(
        self, start_node: str, end_node: str, main_path: List[str], departure_time: datetime,
        max_alternatives: int = 2, penalty_factor: float = 2.0
    ) -> List[List[RouteSegment]]:
        """Find alternative routes by iteratively penalizing path edges and re-running A*."""
        alternatives = []
        if not main_path:
            return alternatives

        temp_graph = self.road_graph.copy()
        paths_found = [main_path]
        end_node_data = temp_graph.nodes[end_node]

        def heuristic(u, v):
            node_u_data = temp_graph.nodes[u]
            dist = self._haversine_distance(node_u_data["lat"], node_u_data["lon"], end_node_data["lat"], end_node_data["lon"])
            return dist / 2.16

        for _ in range(max_alternatives):
            for path in paths_found:
                for i in range(len(path) - 1):
                    u, v = path[i], path[i+1]
                    if temp_graph.has_edge(u, v):
                        temp_graph[u][v]['weight'] *= penalty_factor
            try:
                new_path = nx.astar_path(temp_graph, start_node, end_node, heuristic=heuristic, weight="weight")
                if new_path not in paths_found:
                    segments = self._create_route_segments(new_path, departure_time)
                    alternatives.append(segments)
                    paths_found.append(new_path)
                else:
                    break
            except nx.NetworkXNoPath:
                logger.warning("No more alternative routes could be found.")
                break
            except Exception as e:
                logger.error(f"Error finding an alternative route: {e}")
                break
        return alternatives

    def _calculate_congestion_probability(self, segments: List[RouteSegment]) -> float:
        if not segments:
            return 0.0
        return sum(1 for seg in segments if seg.congestion_score > 0.7) / len(segments)

    def _generate_route_recommendations(
        self, main_route: List[RouteSegment], alternatives: List[List[RouteSegment]], departure_time: datetime
    ) -> List[str]:
        recommendations = []
        if self._calculate_congestion_probability(main_route) > 0.3:
            recommendations.append("High probability of congestion on this route")
            better_time = self._find_better_departure_time(main_route, departure_time)
            if better_time:
                recommendations.append(
                    f"Consider departing at {better_time.strftime('%H:%M')} for better conditions"
                )
        if alternatives:
            alt_durations = [sum(seg.predicted_duration_mins for seg in route) for route in alternatives]
            main_duration = sum(seg.predicted_duration_mins for seg in main_route)
            for i, duration in enumerate(alt_durations):
                if duration < main_duration * 0.9:
                    recommendations.append(
                        f"Alternative route {i + 1} is significantly faster ({int(duration)} mins vs {int(main_duration)} mins)"
                    )
        return recommendations

    def _find_better_departure_time(
        self, route: List[RouteSegment], original_time: datetime, max_delay: int = 120
    ) -> Optional[datetime]:
        best_time, min_congestion = None, float("inf")
        for delay in range(0, max_delay, 15):
            test_time, total_congestion = original_time + timedelta(minutes=delay), 0
            for seg in route:
                conditions = self.predict_segment_conditions(
                    seg.start_lat, seg.start_lon, seg.end_lat, seg.end_lon, test_time
                )
                total_congestion += conditions["congestion_score"]
            if total_congestion < min_congestion:
                min_congestion, best_time = total_congestion, test_time
        return best_time if best_time != original_time else None
