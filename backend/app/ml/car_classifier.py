import numpy as np
import cv2
import logging
from typing import Optional, Tuple, List, Dict
from pathlib import Path

logger = logging.getLogger("app.ml.car_classifier")

class CarClassifier:
    def __init__(self, config: Dict):
        self.config = config
        model_path_rel = config.get("vehicle_detection", {}).get("car_classifier_model_path", "backend/models/Car_Make_Model.tflite")
        project_root = Path(config.get("project_root_dir", ""))
        self.model_path = project_root / model_path_rel
        
        if not self.model_path.exists():
            logger.error(f"Car classifier model not found at {self.model_path}")
            self.interpreter = None
            return

        try:
            import tensorflow as tf
            # FIX: TensorFlow hardware configuration should be moved to a centralized boot script.
            # Removing tf.config.experimental.set_memory_growth from here to avoid RuntimeError
            # if this class is instantiated multiple times or after other TF scripts.
            
            # Check for GPU acceleration config
            use_gpu = self.config.get("performance", {}).get("gpu_acceleration", False)
            delegates = []
            
            if use_gpu:
                try:
                    gpu_delegate = tf.lite.experimental.load_delegate('libtensorflowlite_gpu_delegate.so')
                    delegates.append(gpu_delegate)
                    logger.info("CarClassifier: GPU delegate loaded successfully.")
                except Exception as e:
                    logger.warning(f"CarClassifier: Failed to load GPU delegate, falling back to CPU. Error: {e}")

            self.interpreter = tf.lite.Interpreter(
                model_path=str(self.model_path),
                experimental_delegates=delegates
            )
            self.interpreter.allocate_tensors()
            
            self.input_details = self.interpreter.get_input_details()
            self.output_details = self.interpreter.get_output_details()
            
            self.input_shape = self.input_details[0]['shape']
            self.input_height = self.input_shape[1]
            self.input_width = self.input_shape[2]
            
            logger.info(f"Car classifier loaded. Input: {self.input_width}x{self.input_height}")
            self.labels = self._load_labels()
            
        except (ImportError, OSError) as e:
            logger.error(f"Failed to import tensorflow: {e}. Car classification will be disabled.")
            self.interpreter = None
            return
        except Exception as e:
            logger.error(f"Failed to load car classifier: {e}")
            self.interpreter = None

    def _load_labels(self) -> List[str]:
        labels_path_rel = self.config.get("vehicle_detection", {}).get("car_classifier_labels_path", "backend/models/car_labels.txt")
        project_root = Path(self.config.get("project_root_dir", ""))
        labels_path = project_root / labels_path_rel
        
        if labels_path.exists():
            try:
                with open(labels_path, 'r') as f:
                    labels = [line.strip() for line in f.readlines()]
                logger.info(f"Loaded {len(labels)} labels from {labels_path}")
                return labels
            except Exception as e:
                logger.error(f"Failed to load labels: {e}")
        else:
            logger.warning(f"Labels file not found at {labels_path}")
        return []

    def classify(self, vehicle_crop: np.ndarray) -> Tuple[Optional[str], float]:
        if self.interpreter is None or vehicle_crop.size == 0:
            return None, 0.0
            
        try:
            img = cv2.resize(vehicle_crop, (self.input_width, self.input_height))
            if self.input_details[0]['dtype'] == np.int8:
                input_scale, input_zero_point = self.input_details[0]['quantization']
                if input_scale == 0: input_scale = 1.0
                img = (img.astype(np.float32) / input_scale + input_zero_point).astype(np.int8)
            else:
                img = img.astype(np.float32) / 255.0
                
            img = np.expand_dims(img, axis=0)
            self.interpreter.set_tensor(self.input_details[0]['index'], img)
            self.interpreter.invoke()
            output_tensor = self.interpreter.get_tensor(self.output_details[0]['index'])[0]
            if self.output_details[0]['dtype'] == np.int8:
                output_scale, output_zero_point = self.output_details[0]['quantization']
                output_data = (output_tensor.astype(np.float32) - output_zero_point) * output_scale
            else:
                output_data = output_tensor
                
            max_idx = np.argmax(output_data)
            confidence = float(output_data[max_idx])
            label = f"Class_{max_idx}"
            if self.labels and max_idx < len(self.labels):
                label = self.labels[max_idx]
                
            return label, confidence
            
        except Exception as e:
            logger.error(f"Car classification failed: {e}")
            return None, 0.0
