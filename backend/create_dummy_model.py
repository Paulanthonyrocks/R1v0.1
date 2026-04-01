import os
import sys

# Ensure models directory exists
models_dir = os.path.join(os.path.dirname(__file__), 'models')
os.makedirs(models_dir, exist_ok=True)

try:
    import tensorflow as tf
    print("TensorFlow version:", tf.__version__)
except ImportError:
    print("TensorFlow is not installed. Unable to create h5 model.")
    sys.exit(1)

sequence_length = 12
feature_count = 3  # vehicle_count, average_speed, congestion_score

try:
    model = tf.keras.Sequential([
        # Encoder
        tf.keras.layers.LSTM(32, activation='relu', input_shape=(sequence_length, feature_count), return_sequences=True),
        tf.keras.layers.LSTM(16, activation='relu', return_sequences=False),
        tf.keras.layers.RepeatVector(sequence_length),
        # Decoder
        tf.keras.layers.LSTM(16, activation='relu', return_sequences=True),
        tf.keras.layers.LSTM(32, activation='relu', return_sequences=True),
        tf.keras.layers.TimeDistributed(tf.keras.layers.Dense(feature_count))
    ])
    model.compile(optimizer='adam', loss='mae')
    
    model_path = os.path.join(models_dir, 'anomaly_detector_model.h5')
    # Save in the legacy h5 format
    model.save(model_path, save_format='h5')
    print(f"Successfully generated dummy anomaly detector model at: {model_path}")
except Exception as e:
    print(f"Error creating generating model: {e}")
    sys.exit(1)
