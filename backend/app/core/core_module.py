     1|import cv2
     2|import logging
     3|import time
     4|import numpy as np
     5|import torch
     6|import queue
     7|from multiprocessing import Queue as MPQueue
     8|from typing import Dict, List, Tuple, Optional, Any
     9|from pathlib import Path
    10|from concurrent.futures import ThreadPoolExecutor
    11|
    12|# Modular components
    13|from .detection import DetectionEngine
    14|from .tracking import TrackingManager
    15|from .transforms import CoordinateTransformer
    16|
    17|# Utility imports
    18|try:
    19|    from ..utils.image_processing import LicensePlatePreprocessor
    20|    from ..utils.lane_detection import process_frame_for_lanes, get_lane_boundaries_from_lines
    21|    from ..utils.local_ocr import LocalOCR
    22|    from ..ml.reid_model import ReIDEmbedder
    23|except ImportError:
    24|    logger = logging.getLogger("app.ml")
    25|    logger.error("Error importing utils for CoreModule. System functionality may be limited.")
    26|    LicensePlatePreprocessor = None
    27|    process_frame_for_lanes = None
    28|    get_lane_boundaries_from_lines = None
    29|    LocalOCR = None
    30|    ReIDEmbedder = None
    31|
    32|logger = logging.getLogger("app.ml")
    33|
    34|class CoreModule:
    35|    # Vehicle type mapping
    36|    vehicle_type_map = {
    37|        0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"
    38|    }
    39|
    40|    def __init__(
    41|        self,
    42|        feed_id: str,
    43|        model_path: str,
    44|        config: Dict,
    45|        fps: int,
    46|        db_queue: MPQueue,
    47|        gemini_api_key: Optional[str] = None,
    48|        model_type: str = "yolo",
    49|        preloaded_model: Optional[Any] = None,
    50|        preloaded_reid: Optional[Any] = None,
    51|    ):
    52|        self.feed_id = feed_id
    53|        import copy
    54|        self.config = copy.deepcopy(config)
    55|        self.fps = fps
    56|        self.db_queue = db_queue
    57|        self.gemini_api_key = gemini_api_key
    58|        
    59|        # 1. Configuration sections
    60|        v_cfg = self.config.get("vehicle_detection", {})
    61|        b_cfg = self.config.get("behavior_analysis", {})
    62|        l_cfg = self.config.get("lane_detection", {})
    63|        
    64|        self.project_root = Path(self.config.get("project_root_dir", ""))
    65|        self.model_path = self.project_root / model_path if not Path(model_path).is_absolute() else Path(model_path)
    66|        
    67|        # 2. Thresholds & Params
    68|        self.confidence_threshold = v_cfg.get("confidence_threshold", 0.4)
    69|        self.proximity_threshold = v_cfg.get("proximity_threshold", 60)
    70|        self.predict_timeout = v_cfg.get("predict_timeout", 0.4)
    71|        self.max_active_tracks = v_cfg.get("max_active_tracks", 50)
    72|        
    73|        # 3. Modular Engines Init
    74|        self.device = self._check_gpu_availability()
    75|        self.detector = DetectionEngine(str(self.model_path), self.config, self.device)
    76|        self.detector.load_model()
    77|        
    78|        res = v_cfg.get("frame_resolution", [640, 480])
    79|        self.roi_polygon_points = self.config.get("roi_processing", {})
    80|        self.detector.initialize_roi(res, self.roi_polygon_points)
    81|        
    82|        self.tracker = TrackingManager(self.config, self.fps)
    83|        self.vehicle_data = self.tracker.vehicle_data
    84|        
    85|        calib_cfg = v_cfg.get("calibration", {})
    86|        self.transformer = CoordinateTransformer(calib_cfg)
    87|        self.homography_matrix = None
    88|        self._update_homography(calib_cfg)
    89|        
    90|        # 4. State & Helpers
    91|        self.reid_embedder = preloaded_reid or (ReIDEmbedder(self.config) if v_cfg.get("reid_enabled", True) else None)
    92|        self.ocr_executor = ThreadPoolExecutor(max_workers=2)
    93|        self.ocr_results_queue = queue.Queue()
    94|        
    95|        self.last_detected_lane_lines = None
    96|        self.cached_lane_boundaries = []
    97|        self.last_lane_detection_frame = -1
    98|        self.lane_detection_interval = l_cfg.get("detection_interval", 1.0)
    99|        
   100|        # 5. Behavior & Speed
   101|        self.pixels_per_meter = v_cfg.get("pixels_per_meter", 10.0)
   102|        self.ewma_alpha = b_cfg.get("speed_smoothing_factor", 0.3)
   103|        self.speed_limit = b_cfg.get("speed_limit_kmh", 60)
   104|        self.accel_threshold_mps2 = b_cfg.get("acceleration_threshold_mps2", 2.0)
   105|        self.stopped_speed_threshold_kmh = b_cfg.get("stopped_speed_threshold_kmh", 5.0)
   106|        
   107|        self.preprocessor = None # Gemini
   108|        self.local_ocr = None
   109|        self._reid_updates_this_frame = 0 # Budget control
   110|        
   111|        if self.config.get("ocr_engine", {}).get("enabled", False):
   112|            self._init_ocr()
   113|
   114|    def _check_gpu_availability(self) -> str:
   115|        """Checks for GPU availability for YOLO and engines."""
   116|        if torch.cuda.is_available():
   117|            logger.info(f"[{self.feed_id}] GPU detected. Using CUDA.")
   118|            return "0" 
   119|        return "cpu"
   120|
   121|    def _initialize_roi_mask(self, resolution: List[int]):
   122|        """Initializes ROI and exclusion masks once per resolution change."""
   123|        w, h = resolution
   124|        self.roi_mask = np.ones((h, w), dtype=np.uint8) * 255
   125|        
   126|        if self.roi_polygon_points:
   127|            points_np = np.array(self.roi_polygon_points, dtype=np.int32)
   128|            mask = np.zeros((h, w), dtype=np.uint8)
   129|            cv2.fillPoly(mask, [points_np], 255)
   130|            self.roi_mask = cv2.bitwise_and(self.roi_mask, mask)
   131|        
   132|        exclusion = self.config.get("roi_processing", {})
   133|        if exclusion:
   134|            for zone in exclusion:
   135|                zone_np = (np.array(zone, dtype=np.float32) * [w, h]).astype(np.int32)
   136|                cv2.fillPoly(self.roi_mask, [zone_np], 0)
   137|
   138|    def _update_homography(self, calibration_cfg: Dict):
   139|        """Perspective transformation for distance/speed math."""
   140|        if not calibration_cfg or "image_points" not in calibration_cfg:
   141|            return
   142|        
   143|        img_pts = np.array(calibration_cfg["image_points"], dtype=np.float32)
   144|        world_pts = np.array(calibration_cfg.get("world_points", []), dtype=np.float32)
   145|        
   146|        if len(img_pts) < 4 or len(world_pts) < 4:
   147|            return
   148|            
   149|        res = self.config.get("vehicle_detection", {})
   150|        if np.max(img_pts) <= 1.0:
   151|            img_pts *= [res[0], res[1]]
   152|            
   153|        self.homography_matrix, _ = cv2.findHomography(img_pts, world_pts)
   154|        logger.info(f"[{self.feed_id}] Homography matrix recalibrated.")
   155|
   156|    def _init_ocr(self):
   157|        """Initializes OCR engines based on configuration."""
   158|        ocr_cfg = self.config.get("ocr_engine", {})
   159|        if ocr_cfg.get("use_gemini_ocr", False) and self.gemini_api_key:
   160|            try:
   161|                from ..utils.image_processing import LicensePlatePreprocessor
   162|                self.preprocessor = LicensePlatePreprocessor(self.gemini_api_key)
   163|            except Exception as e:
   164|                logger.error(f"Failed to initialize Gemini OCR: {e}")
   165|        
   166|        if ocr_cfg.get("use_local", True):
   167|            try:
   168|                from ..utils.local_ocr import LocalOCR
   169|                self.local_ocr = LocalOCR(self.config)
   170|            except Exception as e:
   171|                logger.error(f"Failed to initialize Local OCR: {e}")
   172|
   173|    
   174|    def _preprocess_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool, int, int]:
   175|        """
   176|        Preprocesses the frame for inference. 
   177|        Resizes the frame to the detector's target resolution.
   178|        Returns: (processed_frame, roi_enabled, x_offset, y_offset)
   179|        """
   180|        if frame is None:
   181|            return None, False, 0, 0
   182|
   183|        # Get target resolution from detector
   184|        res = self.detector.resolution if self.detector.resolution else [640, 480]
   185|        target_w, target_h = res
   186|        
   187|        # Resize frame to match detector expectations for batching
   188|        processed_frame = cv2.resize(frame, (target_w, target_h))
   189|        
   190|        # ROI is enabled if polygon points are defined
   191|        roi_enabled = self.roi_polygon_points is not None
   192|        
   193|        return processed_frame, roi_enabled, 0, 0
   194|    def detect_and_track(
   195|        self,
   196|        frame: Optional[np.ndarray],
   197|        frame_index: int,
   198|        confidence_threshold: Optional[float] = None,
   199|        proximity_threshold: Optional[int] = None,
   200|        track_timeout: Optional[int] = None,
   201|        external_detections: Optional[List[Tuple]] = None,
   202|        timestamp: Optional[float] = None,
   203|    ) -> Tuple[Dict[str, Dict], List[int], Any]:
   204|        """Orchestrates detection and tracking using modular engines."""
   205|        if frame is None or frame.size == 0:
   206|            return {}, self.cached_lane_boundaries, self.last_detected_lane_lines
   207|
   208|        current_time = timestamp if timestamp is not None else time.time()
   209|        
   210|        # 1. Lane Detection (Periodic)
   211|        if (self.config.get("lane_detection", {}).get("enabled", False) and 
   212|            process_frame_for_lanes and 
   213|            (frame_index - self.last_lane_detection_frame) >= self.lane_detection_interval):
   214|            try:
   215|                lines = process_frame_for_lanes(frame, self.config)
   216|                self.last_detected_lane_lines = lines
   217|                if lines and get_lane_boundaries_from_lines:
   218|                    self.cached_lane_boundaries = get_lane_boundaries_from_lines(frame.shape[1], lines, self.config)
   219|                    self.last_lane_detection_frame = frame_index
   220|            except Exception as e:
   221|                logger.warning(f"Lane detection failed: {e}")
   222|
   223|        # 2. Detection (Skip if external provided)
   224|        if external_detections is not None:
   225|            detections = external_detections
   226|        else:
   227|            detections = self.detector.detect(frame, 0.1)
   228|        
   229|        # 3. Enrichment (ReID Embeddings)
   230|        enriched_detections = []
   231|        for bbox, cls, dconf in detections:
   232|            emb = None
   233|            if self.reid_embedder:
   234|                x1, y1, x2, y2 = map(int, bbox)
   235|                roi = frame[y1:y2, x1:x2]
   236|                if roi.size > 0:
   237|                    emb = self.reid_embedder.extract(roi)
   238|            enriched_detections.append((bbox, cls, dconf, emb))
   239|
   240|        # 4. Tracking
   241|        self.vehicle_data = self.tracker.update(enriched_detections, current_time, frame.shape)
   242|        
   243|        # 5. Metadata Processing
   244|        vis_tracks = {}\n        for tid, track in self.vehicle_data.items():
   245|            cx, cy = (track["bbox"][0] + track["bbox"][2])/2, (track["bbox"][1] + track["bbox"][3])/2
   246|            ground_pos = self.transformer.pixel_to_ground(cx, cy)
   247|            if ground_pos:
   248|                track["ground_coordinates"] = ground_pos
   249|            
   250|            # Simple Filtering for Visualization
   251|            if track["status"] == "active":
   252|                vis_tracks[tid] = track
   253|            elif track["status"] == "predicting":
   254|                if (current_time - track["last_seen"]) < self.predict_timeout:
   255|                    vis_tracks[tid] = track
   256|
   257|        self._save_vehicle_data(vis_tracks)
   258|        self._process_ocr_results()
   259|
   260|        return vis_tracks, self.cached_lane_boundaries, self.last_detected_lane_lines
   261|
   262|    def _save_vehicle_data(self, tracked_vehicles: Dict[str, Dict]):
   263|        for vehicle_id, data in tracked_vehicles.items():
   264|            if self.db_queue:
   265|                try:
   266|                    now = time.time()
   267|                    self.db_queue.put_nowait({
   268|                        "type": "vehicle_data",
   269|                        "feed_id": self.feed_id,
   270|                        "vehicle_id": str(vehicle_id),
   271|                        "timestamp": float(now),
   272|                        "bbox": [float(x) for x in data["bbox"]],
   273|                        "centroid": [float(x) for x in data["centroid"]],
   274|                        "speed": float(data.get("speed", 0.0)),
   275|                        "license_plate": str(data.get("license_plate", "Unknown")),
   276|                        "class_id": int(data["class_id"]),
   277|                        "class_name": str(self.vehicle_type_map.get(data["class_id"], "unknown")),
   278|                        "confidence": float(data["confidence"]),
   279|                        "status": str(data["status"]),
   280|                        "lane": int(data.get("lane", -1)),
   281|                    })
   282|                except queue.Full:
   283|                    pass
   284|
   285|    def _process_ocr_results(self):
   286|        """Drains the OCR results queue and updates vehicle data."""
   287|        try:
   288|            while True:
   289|                result = self.ocr_results_queue.get_nowait()
   290|                tid = result["track_id"]
   291|                if tid in self.vehicle_data:
   292|                    self.vehicle_data[tid]["license_plate"] = result["plate_text"]
   293|        except queue.Empty:
   294|            pass
   295|
   296|    def cleanup(self):
   297|        """Shutdown thread pools."""
   298|        if self.ocr_executor:
   299|            self.ocr_executor.shutdown(wait=True)
   300|
   301|    def update_config(self, updates: Dict[str, Any]):
   302|        """Dynamically updates configuration."""
   303|        if "vehicle_detection" in updates:
   304|            v_cfg = updates["vehicle_detection"]
   305|            self.confidence_threshold = v_cfg.get("confidence_threshold", self.confidence_threshold)
   306|            if "calibration" in v_cfg:
   307|                self._update_homography(v_cfg["calibration"])
   308|                self.transformer.update_calibration(v_cfg["calibration"])
   309|        
   310|        if "roi" in updates:
   311|            # Handle ROI updates
   312|            pass
   313|