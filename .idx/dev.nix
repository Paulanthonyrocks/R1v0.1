{pkgs}: {
  channel = "stable-24.05"; # Using a recent stable channel

  packages = [
    pkgs.nodejs_20
    pkgs.tesseract
    pkgs.opencv4 # System OpenCV library
    pkgs.docker-compose
    pkgs.sudo

    # Define the Python environment with packages managed by Nix
    (pkgs.python311.withPackages (ps: [
      # --- Core FastAPI & Web ---
      ps.fastapi
      ps.uvicorn
      ps.python-multipart
      ps.httpx # For async HTTP calls
      ps.python-jose # Includes cryptography
      ps.passlib   # For password hashing
      ps.bcrypt    # Often used with passlib for bcrypt support
      ps.pydantic  # Explicitly include for version consistency

      # --- Configuration & Utilities ---
      ps.pyyaml
      ps.psutil
      ps.numpy
      ps.tenacity
      ps.aiofiles

      # --- Kafka ---
      ps.kafka-python

      # --- Database ---
      ps.pymongo
      ps.redis # Python client for Redis

      # --- ML/CV Dependencies (Managed by Nix where possible) ---
      ps.pytorch
      ps.torchvision
      ps.torchaudio
      # ultralytics will be installed via pip (see requirements.txt)
      ps.opencv4 # Python bindings for OpenCV
      ps.scipy
      # filterpy will be installed via pip (see requirements.txt)
      ps.tensorflow-cpu # CPU-only version of TensorFlow
      ps.scikitlearn
      ps.scikit-image
      ps.pandas
      ps.aiohttp # For async HTTP requests

      # --- OCR Dependencies ---
      ps.pillow
      ps.pytesseract
      ps.google-generativeai

      # --- Utilities ---
      ps.pip # Needed for installing requirements from requirements.txt
      # ps.wheel # Usually not needed unless building wheels directly
      # ps.setuptools # Usually pulled in as a dependency if needed

    ])) # End of python311.withPackages
  ]; # End of packages

  idx.extensions = [
    "esbenp.prettier-vscode",
    "GitHub.vscode-pull-request-github",
    "ms-pyright.pyright",
    "ms-python.debugpy",
    "ms-python.python",
    "ms-toolsai.jupyter",
    "ms-toolsai.jupyter-keymap",
    "ms-toolsai.jupyter-renderers",
    "ms-toolsai.vscode-jupyter-cell-tags",
    "ms-toolsai.vscode-jupyter-slideshow",
    "ms-vscode.js-debug"
   ]; # End of idx.extensions

  idx.previews = {
    enable = true;
    previews = {
      backend = {
        command = [
          "/bin/sh"
          "-c"
          "cd backend && uvicorn app.main:app --host 0.0.0.0 --port 9002 --reload"
        ];
        manager = "web";
      }; # end backend preview

      frontend = {
        command = [
          "/bin/sh"
          "-c"
          "cd frontend && npm run dev -- --port 3000 --hostname 0.0.0.0"
        ];
        manager = "web";
        env = {
          NEXT_PUBLIC_API_URL = "localhost:9002";
        };
      }; # end frontend preview
    };
  }; # end idx.previews

  # Workspace lifecycle hooks
  idx.workspace = {
    onCreate = {
      npm-install = "cd frontend && npm install";
      # Install Python dependencies NOT managed by Nix from both requirement files
      pip-installs = ''
        cd backend && \
        echo "Installing packages from requirements.txt and firebase_requirements.txt via pip..." && \
        pip install --no-cache-dir -r firebase_requirements.txt && \
        echo "Pip installations complete."
      '';
    }; # end onCreate

    onStart = {
      log-start = "echo Nix environment ready. Starting previews...";
      check-files = "ls -l frontend/package.json backend/app/main.py backend/firebase_requirements.txt || true";
    }; # end onStart
  }; # End of idx.workspace

} # End of main function