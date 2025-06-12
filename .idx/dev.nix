{pkgs}:

{
  channel = "stable-24.05"; # Using a recent stable channel

  # Define all system-level packages needed in the environment
  packages = [
    # --- System Tools ---
    pkgs.nodejs_20
    pkgs.tesseract     # System Tesseract library
    pkgs.opencv4       # System OpenCV library
    pkgs.docker-compose
    pkgs.sudo

    # --- Python Runtime ---
    pkgs.python311
    pkgs.python311Packages.pip

    # --- Core FastAPI & Web ---
    pkgs.python311Packages.fastapi
    pkgs.python311Packages.uvicorn
    pkgs.python311Packages.python-multipart
    pkgs.python311Packages.httpx
    pkgs.python311Packages.python-jose # Includes cryptography
    pkgs.python311Packages.passlib    # For password hashing
    pkgs.python311Packages.bcrypt     # Often used with passlib for bcrypt support
    pkgs.python311Packages.pydantic

    # --- Configuration & Utilities ---
    pkgs.python311Packages.pyyaml
    pkgs.python311Packages.psutil
    pkgs.python311Packages.numpy
    pkgs.python311Packages.tenacity
    pkgs.python311Packages.aiofiles
    pkgs.python311Packages.firebase-admin

    # --- Kafka ---
    pkgs.python311Packages.kafka-python

    # --- Database & Caching ---
    pkgs.python311Packages.pymongo     # For MongoDB support
    pkgs.python311Packages.redis       # Python client for Redis

    # --- ML/CV Dependencies ---
    pkgs.python311Packages.pytorch      # Core PyTorch library
    pkgs.python311Packages.torchvision  # For computer vision tasks with PyTorch
    pkgs.python311Packages.torchaudio   # For audio tasks with PyTorch
    pkgs.python311Packages.opencv4      # Python bindings for OpenCV (equivalent to opencv-python)
    pkgs.python311Packages.scipy
    pkgs.python311Packages.scikit-learn  # Scikit-learn
    pkgs.python311Packages.scikit-image # Scikit-image
    pkgs.python311Packages.pandas       # Data manipulation

    # --- OCR Dependencies ---
    pkgs.python311Packages.pillow       # Python Imaging Library
    pkgs.python311Packages.pytesseract  # Python wrapper for Tesseract
    pkgs.python311Packages.google-generativeai # Google Generative AI client

    # --- Additional Utilities ---
    pkgs.python311Packages.aiohttp      # For async HTTP requests
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
    }; # end onCreate

    onStart = {
      log-start = "echo Nix environment ready. Starting previews...";
      # Verify critical files exist
      check-files = "ls -l frontend/package.json backend/app/main.py || true";
    }; # end onStart
  }; # End of idx.workspace

} # End of main function
