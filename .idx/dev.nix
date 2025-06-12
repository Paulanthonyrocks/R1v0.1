{ pkgs, ... }:

let
  # Poetry-to-Nix to build Python packages not in nixpkgs
  poetry2nix = pkgs.callPackage (fetchTarball "https://github.com/nix-community/poetry2nix/archive/refs/tags/1.40.1.tar.gz") { };
in
{
  # Using a recent stable channel
  channel = "stable-24.05";

  # Define all system-level packages needed in the environment
  packages = [
    # --- System Tools ---
    pkgs.nodejs_20
    pkgs.tesseract     # System Tesseract library
    pkgs.opencv4       # System OpenCV library
    pkgs.docker-compose
    pkgs.sudo

    # --- Python Runtime and Packages ---
    (pkgs.python311.withPackages (ps: with ps; [
      pip

      # --- Core FastAPI & Web ---
      fastapi
      uvicorn
      python-multipart
      httpx
      python-jose # Includes cryptography
      passlib    # For password hashing
      bcrypt     # Often used with passlib for bcrypt support
      pydantic

      # --- Configuration & Utilities ---
      pyyaml
      psutil
      numpy
      tenacity
      aiofiles

      # --- Kafka ---
      kafka-python

      # --- Database & Caching ---
      pymongo     # For MongoDB support
      redis       # Python client for Redis

      # --- ML/CV Dependencies ---
      pytorch      # Core PyTorch library
      torchvision  # For computer vision tasks with PyTorch
      torchaudio   # For audio tasks with PyTorch
      opencv-python # Python bindings for OpenCV
      scipy
      scikit-learn  # Scikit-learn
      scikit-image # Scikit-image
      pandas       # Data manipulation

      # --- OCR Dependencies ---
      pillow       # Python Imaging Library
      pytesseract  # Python wrapper for Tesseract

      # --- Additional Utilities ---
      aiohttp      # For async HTTP requests
    ] ++ [
      # Packages from Pip using poetry2nix
      (poetry2nix.buildPoetryApplication {
        projectDir = ./backend; # Assuming pyproject.toml is in the backend directory
        overrides = [
          (poetry2nix.defaultPoetryOverrides.overridePythonAttrs (old: {
            # Add any required overrides here if needed
          }))
        ];
      })
    ]))
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
        id = "backend";
        command = [
          "/bin/sh"
          "-c"
          "cd backend && uvicorn app.main:app --host 0.0.0.0 --port 9002 --reload"
        ];
        manager = "web";
      }; # end backend preview

      frontend = {
        id = "frontend";
        command = [
          "/bin/sh"
          "-c"
          "cd frontend && npm run dev -- --port 3000 --hostname 0.0.0.0"
        ];
        manager = "web";
        env = {
          # Correctly reference the backend preview URL
          NEXT_PUBLIC_API_URL = ''${previews.backend.url}'';
        };
      }; # end frontend preview
    };
  }; # end idx.previews

  # Workspace lifecycle hooks for Project IDX
  idx.workspace = {
    onCreate = {
      npm-install = "cd frontend && npm install";
      # pip-install-firebase is no longer needed
    }; # end onCreate

    onStart = {
      log-start = "echo Nix environment ready. Starting previews...";
      # Verify critical files exist
      check-files = "ls -l frontend/package.json backend/app/main.py || true";
    }; # end onStart
  }; # End of idx.workspace

}
