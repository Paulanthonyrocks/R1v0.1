# dev.nix

{pkgs}:

let
  # Define the Python environment with ALL necessary packages managed by Nix
  # This makes the environment fully declarative and reproducible.
  pythonEnvironment = pkgs.python311.withPackages (ps: [
    # --- Core FastAPI & Web ---
    ps.fastapi
    ps.uvicorn
    ps.python-multipart
    ps.httpx
    ps.python-jose # Includes cryptography
    ps.passlib    # For password hashing
    ps.bcrypt     # Often used with passlib for bcrypt support
    ps.pydantic

    # --- Configuration & Utilities ---
    ps.pyyaml
    ps.psutil
    ps.numpy
    ps.tenacity
    ps.aiofiles

    # --- Kafka ---
    ps.kafka-python

    # --- Database & Caching ---
    ps.pymongo     # For MongoDB support
    ps.redis       # Python client for Redis
    ps.firebase-admin # For Firebase Admin SDK

    # --- ML/CV Dependencies ---
    ps.pytorch      # Core PyTorch library
    ps.torchvision  # For computer vision tasks with PyTorch
    ps.torchaudio   # For audio tasks with PyTorch
    ps.ultralytics  # YOLO models (now managed by Nix!)
    ps.opencv4      # Python bindings for OpenCV (equivalent to opencv-python)
    ps.scipy
    ps.filterpy     # FilterPy library (now managed by Nix!)
    ps.tensorflow-cpu # CPU-only version of TensorFlow
    ps.scikitlearn  # Scikit-learn
    ps.scikit-image # Scikit-image
    ps.pandas       # Data manipulation

    # --- OCR Dependencies ---
    ps.pillow       # Python Imaging Library
    ps.pytesseract  # Python wrapper for Tesseract
    ps.google-generativeai # Google Generative AI client

    # --- Additional Utilities ---
    ps.aiohttp      # For async HTTP requests
    # ps.pip # Not strictly needed if all deps are Nix-managed, but harmless to keep for interactive use if desired
  ]);

in
{
  channel = "stable-24.05"; # Using a recent stable channel

  # Define all system-level packages needed in the environment
  packages = [
    pkgs.nodejs_20
    pkgs.tesseract     # System Tesseract library
    pkgs.opencv4       # System OpenCV library
    pkgs.docker-compose
    pkgs.sudo
    pythonEnvironment  # Include the fully defined Python environment here
  ];

  # VS Code extensions for Project IDX
  idx.extensions = [
    "esbenp.prettier-vscode"
    "GitHub.vscode-pull-request-github"
    "ms-pyright.pyright"
    "ms-python.debugpy"
    "ms-python.python"
    "ms-toolsai.jupyter"
    "ms-toolsai.jupyter-keymap"
    "ms-toolsai.jupyter-renderers"
    "ms-toolsai.vscode-jupyter-cell-tags"
    "ms-toolsai.vscode-jupyter-slideshow"
    "ms-vscode.js-debug"
  ];

  # Preview configurations for Project IDX
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
          NEXT_PUBLIC_API_URL = "localhost:9002"; # Ensure this matches the backend preview's accessible URL
        };
      }; # end frontend preview
    };
  }; # end idx.previews

  # Workspace lifecycle hooks for Project IDX
  idx.workspace = {
    onCreate = {
      npm-install = "cd frontend && npm install";
      # The 'pip-installs' step has been removed as all Python dependencies
      # are now declaratively managed by Nix within the 'pythonEnvironment'.
    }; # end onCreate

    onStart = {
      log-start = "echo Nix environment ready. Starting previews...";
      # Verify critical files exist; backend/requirements.txt is no longer needed
      check-files = "ls -l frontend/package.json backend/app/main.py || true";
    }; # end onStart
  }; # End of idx.workspace

} # End of main function