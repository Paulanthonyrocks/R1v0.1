File unchanged since last read. The content from the earlier read_file result in this conversation is still current — refer to that instead of re-reading.

    def _preprocess_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, bool, int, int]:
        """
        Preprocesses the frame for inference. 
        Can be extended to implement ROI cropping to improve performance/accuracy.
        Returns: (processed_frame, roi_enabled, x_offset, y_offset)
        """
        # Currently, we use the full frame.
        return frame, False, 0, 0
