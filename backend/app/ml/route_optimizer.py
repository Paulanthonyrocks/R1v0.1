import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import numpy as np
from dataclasses import dataclass
import networkx as nx

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

class RouteOptimizer:
    def __init__(self, traffic_predictor, data_cache):
        self.traffic_predictor = traffic_predictor
        self.data_cache = data_cache
        self.road_graph = nx.DiGraph()
        self._initialize_road_graph()

    def _initialize_road_graph(self):
        """Initialize the road network graph with default weights"""
        # TODO: Load actual road network data
        # For now, using a simple grid network for demonstration
        self._create_sample_grid_network()

    def _create_sample_grid_network(self):
        """Create a sample grid network for testing"""
        # Create a 5x5 grid of nodes
        for i in range(5):
            for j in range(5):
                self.road_graph.add_node(f"{i},{j}", 
                                       lat=34.0 + i*0.01, 
                                       lon=-118.0 + j*0.01)

        # Connect adjacent nodes
        for i in range(5):
            for j in range(5):
                if i < 4:  # Vertical connections
                    self.road_graph.add_edge(f"{i},{j}", f"{i+1},{j}", 
                                           weight=1.0, 
                                           distance_km=1.0)
                if j < 4:  # Horizontal connections
                    self.road_graph.add_edge(f"{i},{j}", f"{i},{j+1}", 
                                           weight=1.0, 
                                           distance_km=1.0)

    def predict_segment_conditions(self, 
                                start_lat: float, 
                                start_lon: float,
                                end_lat: float, 
                                end_lon: float,
                                prediction_time: datetime) -> Dict[str, Any]:
        """Predict traffic conditions for a route segment"""
        # Get historical data for the segment
        segment_stats = self.data_cache.get_statistics(
            latitude=(start_lat + end_lat) / 2,
            longitude=(start_lon + end_lon) / 2,
            hours=24
        )

        # Get prediction for the segment
        prediction = self.traffic_predictor.predict_incident_likelihood({
            'latitude': (start_lat + end_lat) / 2,
            'longitude': (start_lon + end_lon) / 2,
            'prediction_time': prediction_time
        })

        # Calculate segment metrics
        base_duration = self._calculate_base_duration(
            start_lat, start_lon, end_lat, end_lon
        )
        
        congestion_factor = 1.0 + (prediction['incident_likelihood'] * 2)
        predicted_duration = base_duration * congestion_factor

        return {
            'predicted_duration_mins': predicted_duration,
            'congestion_score': prediction['incident_likelihood'],
            'confidence': prediction.get('confidence_score', 0.7),
            'typical_conditions': segment_stats
        }

    def _calculate_base_duration(self, 
                               start_lat: float, 
                               start_lon: float, 
                               end_lat: float, 
                               end_lon: float) -> float:
        """Calculate base duration for a segment based on distance"""
        # Simple distance-based calculation (assume 60 km/h average speed)
        distance = self._haversine_distance(
            start_lat, start_lon, end_lat, end_lon
        )
        return (distance / 60.0) * 60  # Convert to minutes

    def _haversine_distance(self, 
                          lat1: float, 
                          lon1: float, 
                          lat2: float, 
                          lon2: float) -> float:
        """Calculate the distance between two points using Haversine formula"""
        R = 6371  # Earth's radius in kilometers

        lat1_rad = np.radians(lat1)
        lon1_rad = np.radians(lon1)
        lat2_rad = np.radians(lat2)
        lon2_rad = np.radians(lon2)

        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad

        a = np.sin(dlat/2)**2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon/2)**2
        c = 2 * np.arcsin(np.sqrt(a))
        
        return R * c

    def optimize_route(self,
                      start_lat: float,
                      start_lon: float,
                      end_lat: float,
                      end_lon: float,
                      departure_time: datetime,
                      consider_alternatives: bool = True) -> OptimizedRoute:
        """Find the optimal route considering predicted traffic conditions"""
        try:
            # Update graph weights based on predictions
            self._update_graph_weights(departure_time)

            # Find the best route
            path = self._find_optimal_path(start_lat, start_lon, end_lat, end_lon)
            route_segments = self._create_route_segments(path, departure_time)

            # Calculate route metrics
            total_distance = sum(seg.distance_km for seg in route_segments)
            total_duration = sum(seg.predicted_duration_mins for seg in route_segments)
            avg_confidence = np.mean([seg.confidence for seg in route_segments])

            # Find alternative routes if requested
            alternatives = []
            if consider_alternatives:
                alternatives = self._find_alternative_routes(
                    start_lat, start_lon, end_lat, end_lon, departure_time
                )

            # Generate recommendations
            recommendations = self._generate_route_recommendations(
                route_segments, alternatives, departure_time
            )

            return OptimizedRoute(
                segments=route_segments,
                total_distance_km=total_distance,
                estimated_duration_mins=total_duration,
                confidence_score=avg_confidence,
                alternative_routes=alternatives,
                congestion_probability=self._calculate_congestion_probability(route_segments),
                recommendations=recommendations
            )

        except Exception as e:
            logger.error(f"Error optimizing route: {e}")
            raise

    def _update_graph_weights(self, prediction_time: datetime):
        """Update graph edge weights based on predicted conditions"""
        for u, v, data in self.road_graph.edges(data=True):
            node_u = self.road_graph.nodes[u]
            node_v = self.road_graph.nodes[v]
            
            conditions = self.predict_segment_conditions(
                node_u['lat'], node_u['lon'],
                node_v['lat'], node_v['lon'],
                prediction_time
            )
            
            # Update edge weight based on predicted duration
            self.road_graph[u][v]['weight'] = conditions['predicted_duration_mins']
            self.road_graph[u][v]['congestion_score'] = conditions['congestion_score']

    def _find_optimal_path(self, 
                          start_lat: float, 
                          start_lon: float, 
                          end_lat: float, 
                          end_lon: float) -> List[str]:
        """Find the optimal path in the graph"""
        start_node = self._find_nearest_node(start_lat, start_lon)
        end_node = self._find_nearest_node(end_lat, end_lon)
        
        try:
            path = nx.shortest_path(
                self.road_graph, 
                start_node, 
                end_node, 
                weight='weight'
            )
            return path
        except nx.NetworkXNoPath:
            raise ValueError("No route found between the specified points")

    def _find_nearest_node(self, lat: float, lon: float) -> str:
        """Find the nearest node in the graph to the given coordinates"""
        min_dist = float('inf')
        nearest_node = None
        
        for node, data in self.road_graph.nodes(data=True):
            dist = self._haversine_distance(
                lat, lon, data['lat'], data['lon']
            )
            if dist < min_dist:
                min_dist = dist
                nearest_node = node
                
        return nearest_node

    def _create_route_segments(self, 
                             path: List[str], 
                             departure_time: datetime) -> List[RouteSegment]:
        """Create route segments from a path"""
        segments = []
        for i in range(len(path) - 1):
            u, v = path[i], path[i+1]
            node_u = self.road_graph.nodes[u]
            node_v = self.road_graph.nodes[v]
            
            edge_data = self.road_graph[u][v]
            conditions = self.predict_segment_conditions(
                node_u['lat'], node_u['lon'],
                node_v['lat'], node_v['lon'],
                departure_time + timedelta(minutes=sum(seg.predicted_duration_mins for seg in segments))
            )
            
            segment = RouteSegment(
                start_lat=node_u['lat'],
                start_lon=node_u['lon'],
                end_lat=node_v['lat'],
                end_lon=node_v['lon'],
                distance_km=edge_data['distance_km'],
                typical_duration_mins=edge_data['distance_km'] * 60 / 60,  # Assuming 60 km/h
                predicted_duration_mins=conditions['predicted_duration_mins'],
                congestion_score=conditions['congestion_score'],
                confidence=conditions['confidence']
            )
            segments.append(segment)
            
        return segments

    def _find_alternative_routes(self,
                               start_lat: float,
                               start_lon: float,
                               end_lat: float,
                               end_lon: float,
                               departure_time: datetime,
                               max_alternatives: int = 2) -> List[List[RouteSegment]]:
        """Find alternative routes"""
        alternatives = []
        start_node = self._find_nearest_node(start_lat, start_lon)
        end_node = self._find_nearest_node(end_lat, end_lon)
        
        try:
            # Find k-shortest paths
            paths = list(nx.shortest_simple_paths(
                self.road_graph, 
                start_node, 
                end_node, 
                weight='weight'
            ))
            
            # Convert paths to route segments
            for path in paths[1:max_alternatives+1]:  # Skip the first path (main route)
                segments = self._create_route_segments(path, departure_time)
                alternatives.append(segments)
                
        except nx.NetworkXNoPath:
            logger.warning("No alternative routes found")
            
        return alternatives

    def _calculate_congestion_probability(self, segments: List[RouteSegment]) -> float:
        """Calculate the probability of encountering significant congestion"""
        high_congestion_segments = sum(1 for seg in segments if seg.congestion_score > 0.7)
        return high_congestion_segments / len(segments) if segments else 0.0

    def _generate_route_recommendations(self,
                                     main_route: List[RouteSegment],
                                     alternatives: List[List[RouteSegment]],
                                     departure_time: datetime) -> List[str]:
        """Generate recommendations for the route"""
        recommendations = []
        
        # Analyze main route congestion
        congestion_prob = self._calculate_congestion_probability(main_route)
        if congestion_prob > 0.3:
            recommendations.append("High probability of congestion on this route")
            
            # Suggest better departure time
            better_time = self._find_better_departure_time(
                main_route, departure_time
            )
            if better_time:
                recommendations.append(
                    f"Consider departing at {better_time.strftime('%H:%M')} "
                    "for better conditions"
                )

        # Compare with alternatives
        if alternatives:
            alt_durations = [sum(seg.predicted_duration_mins for seg in route) 
                           for route in alternatives]
            main_duration = sum(seg.predicted_duration_mins for seg in main_route)
            
            for i, duration in enumerate(alt_durations):
                if duration < main_duration * 0.9:  # At least 10% faster
                    recommendations.append(
                        f"Alternative route {i+1} is significantly faster "
                        f"({int(duration)} mins vs {int(main_duration)} mins)"
                    )

        return recommendations

    def _find_better_departure_time(self,
                                  route: List[RouteSegment],
                                  original_time: datetime,
                                  max_delay: int = 120) -> Optional[datetime]:
        """Find a better departure time within the next max_delay minutes"""
        best_time = None
        min_congestion = float('inf')
        
        for delay in range(0, max_delay, 15):  # Check every 15 minutes
            test_time = original_time + timedelta(minutes=delay)
            total_congestion = 0
            
            for seg in route:
                conditions = self.predict_segment_conditions(
                    seg.start_lat, seg.start_lon,
                    seg.end_lat, seg.end_lon,
                    test_time
                )
                total_congestion += conditions['congestion_score']
            
            if total_congestion < min_congestion:
                min_congestion = total_congestion
                best_time = test_time
                
        return best_time if best_time != original_time else None
