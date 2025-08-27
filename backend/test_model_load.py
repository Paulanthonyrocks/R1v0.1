import tensorflow as tf
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

model_path = "/home/user/R1v0.1/backend/models/traffic_predictor_model.h5"

# Ensure the path is absolute for clarity, though relative might work from project root
absolute_model_path = os.path.abspath(model_path)

logger.info(f"Attempting to load model from: {absolute_model_path}")

try:
    model = tf.keras.models.load_model(absolute_model_path)
    logger.info("Model loaded successfully!")
    # Optionally, print model summary to confirm
    model.summary()
except Exception as e:
    logger.error(f"Failed to load model: {e}", exc_info=True)
