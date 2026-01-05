# /content/drive/MyDrive/R1v0.1/backend/app/utils/utils.py

# Imports needed for check_system_resources or the __main__ block
import logging

# Imports strictly for the __main__ example block (if not already covered)
import sys
from pathlib import Path  # Used in __main__ for config path

# Third-party imports potentially used in __main__ (if not already covered)
import cv2  # Used in __main__ for dummy image creation
import numpy as np  # Used in __main__ for dummy image creation & np.zeros
# Unused imports like asyncio, sqlite3, queue, threading, re, yaml, io, collections.deque,
# functools.lru_cache, tenacity, torch, PIL.Image, pytesseract, google.generativeai,
# google.api_core.exceptions, multiprocessing.Queue, other typing hints (Dict, Any, etc.),
# pymongo, sqlalchemy components, contextlib components will be removed if not used by retained code.

# Logging setup
logger = logging.getLogger(__name__)

# ----- System Resources -----
# check_system_resources function moved to monitoring.py

# --- Custom Exceptions ---
# DatabaseError moved to backend/app/utils/database.py

# FrameTimer class moved to backend/app/utils/video.py
# FrameReader class moved to backend/app/utils/video.py

# TrafficMonitor class moved to backend/app/utils/monitoring.py

# Visualization functions (create_lane_overlay, create_grid_overlay, alpha_blend, visualize_data)
# and related global variables (cached_lane_overlay, etc.)
# moved to backend/app/utils/visualization.py

# LicensePlatePreprocessor class moved to backend/app/utils/image_processing.py

# DatabaseManager class moved to backend/app/utils/database.py


# --- Example Usage (Optional: for testing utils directly) ---
if __name__ == "__main__":
    # Adjusted imports for moved components
    from backend.app.utils.config import load_config, ConfigError
    from backend.app.utils.image_processing import (
        LicensePlatePreprocessor,
    )  # New import

    print("Running utils.py directly (for testing purposes)...")
    try:
        project_root_dir = Path(__file__).resolve().parent.parent.parent
        # Standard config file name, adjust if your project uses a different one
        config_file_path_main_try = project_root_dir / "config.yaml"

        # Fallback for alternative CWDs or slightly different structures
        # This logic helps if the script is run from various locations.
        if not config_file_path_main_try.exists():
            alt_config_paths = [
                Path("config.yaml"),  # CWD is project root
                Path(
                    "../../../config.yaml"
                ).resolve(),  # CWD is utils (backend/app/utils)
                Path("../../config.yaml").resolve(),  # CWD is app (backend/app)
                Path("../config.yaml").resolve(),  # CWD is backend (backend)
            ]
            for p in alt_config_paths:
                if p.exists():
                    config_file_path_main_try = p
                    break
            if not config_file_path_main_try.exists():
                print(
                    f"Warning: config.yaml not found in typical locations relative to {Path.cwd()} or script dir."
                )
                # As a last resort, try to create a default one if possible or use embedded defaults.
                # For this test script, we'll proceed with empty config if none found after extensive search.
                # config_file_path_main_try = None # Or set to a default path where it *should* be
                # For now, we let load_config handle the case where the file doesn't exist if path is None/invalid

        print(f"Attempting to load config from: {config_file_path_main_try}")
        # load_config will use its internal defaults if config_file_path_main_try is None or file not found
        config_data = load_config(
            config_file_path_main_try
            if config_file_path_main_try and config_file_path_main_try.exists()
            else Path("non_existent_config_to_force_defaults.yaml")
        )

        print("\n--- Configuration Loaded (using new config module) ---")
        print(
            f"Database path from config: {config_data.get('database', {}).get('db_path')}"
        )
        print(
            f"Model path from config: {config_data.get('vehicle_detection', {}).get('model_path')}"
        )
        gemini_key = config_data.get("ocr_engine", {}).get("gemini_api_key")
        print(
            f"Gemini Key Set in config: {'Yes' if gemini_key and gemini_key.strip() else 'No'}"
        )

        print(
            "\n--- Testing LicensePlatePreprocessor (Gemini - with config from new module) ---"
        )
        lp_preprocessor = LicensePlatePreprocessor(
            config_data
        )  # Instantiated from new import
        dummy_roi = np.zeros((100, 300, 3), dtype=np.uint8)
        cv2.putText(
            dummy_roi, "TEST", (50, 70), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3
        )
        print("Attempting OCR on a dummy image with Gemini...")
        ocr_text_gemini = lp_preprocessor.preprocess_and_ocr(dummy_roi)
        print(
            f"Gemini Dummy OCR Result: '{ocr_text_gemini}' (Empty is OK if key invalid/quota hit)"
        )

        print(
            "--- Testing LicensePlatePreprocessor (Tesseract Fallback - with config from new module) ---"
        )
        # Ensure config_data is passed even if Gemini key is missing, for other Tesseract settings
        lp_preprocessor_tess = LicensePlatePreprocessor(
            config_data
        )  # Instantiated from new import
        dummy_roi_tess = np.zeros((100, 300, 3), dtype=np.uint8)
        cv2.rectangle(dummy_roi_tess, (10, 10), (290, 90), (50, 50, 50), -1)
        cv2.putText(
            dummy_roi_tess,
            "TST123",
            (40, 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            2,
            (220, 220, 220),
            5,
            cv2.LINE_AA,
        )
        print("Attempting OCR on a dummy image with Tesseract...")
        ocr_text_tesseract = lp_preprocessor_tess.preprocess_and_ocr(dummy_roi_tess)
        print(
            f"Tesseract Dummy OCR Result: '{ocr_text_tesseract}' (Requires Tesseract installed & configured)"
        )

    except ConfigError as ce:
        print(f"\n--- CONFIGURATION ERROR ---\n{ce}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n--- UNEXPECTED ERROR IN MAIN TEST BLOCK ---\n{exc}")
        logger.error("Main test block error", exc_info=True)
        sys.exit(1)
    print("\n--- Utils.py Tests Finished ---")
