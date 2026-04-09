def estimate_motion(self, frame: np.ndarray) -> Tuple[float, float]:
    """Returns (dx, dy) shift from previous frame."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    dx, dy = 0.0, 0.0
    h, w = gray.shape[:2]

    if self.prev_gray is not None and self.prev_pts is not None:
        # Calculate optical flow
        next_pts, status, error = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, self.prev_pts, None, **self.lk_params)
        
        if next_pts is not None:
            good_new = next_pts[status == 1]
            good_old = self.prev_pts[status == 1]
            
            if len(good_new) > 10:
                # Calculate average displacement
                diff = good_new - good_old
                dx, dy = np.median(diff, axis=0)
                
                # Fix: Use resolution-dependent thresholds to filter unrealistic camera shake
                # Cap max frame-to-frame drift to 5% of screen dimensions
                if abs(dx) > (w * 0.05) or abs(dy) > (h * 0.05):
                    dx, dy = 0.0, 0.0
    
    # Update for next frame
    # We re-detect features periodically or if we have too few
    if self.prev_pts is None or len(self.prev_pts) < 20:
        self.prev_pts = cv2.goodFeaturesToTrack(gray, maxCorners=self.max_features, qualityLevel=0.3, minDistance=7, blockSize=7)
    else:
        # Keep the features we just tracked to ensure continuity
        # But we might need to refresh them occasionally to avoid tracking moving objects
        self.prev_pts = cv2.goodFeaturesToTrack(gray, maxCorners=self.max_features, qualityLevel=0.3, minDistance=7, blockSize=7)

    self.prev_gray = gray
    return float(dx), float(dy)
