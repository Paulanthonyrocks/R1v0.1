{pkgs}:

let
  pythonOverlay = self: super: {
    ultralytics = super.callPackage (import (pkgs.fetchFromGitHub {
      owner = "NixOS";
      repo = "nixpkgs";
      rev = "20c7e42c35ff601f464782ca3042f53466db01e3"; # A recent commit with ultralytics
      sha256 = "sha256-c41f6c1f34e5c610b8b3f39a2b4b4b6f6d8b0b8b4d4e4d4e4d4e4d4e4d4e4d4e"; # This needs to be updated to the actual hash of the fetched source
    }) { pkgs = super; }) {};
    filterpy = super.callPackage (pkgs.python3Packages.buildPythonPackage {
      pname = "filterpy";
      version = "1.4.5"; # Replace with the desired version
      src = super.fetchPypi {
        pname = "filterpy";
        version = "1.4.5"; # Replace with the desired version
        sha256 = "sha256-put_filterpy_sha256_here"; # Replace with the actual sha256 hash
      };
    }) {};
    firebase-admin = super.callPackage (pkgs.python3Packages.buildPythonPackage {
      pname = "firebase-admin";
      version = "6.2.0"; # Replace with the desired version
      src = super.fetchPypi {
        pname = "firebase-admin";
        version = "6.2.0"; # Replace with the desired version
        sha256 = "sha256-put_firebase-admin_sha256_here"; # Replace with the actual sha256 hash
      };
    }) {};
  };
  pythonPackages = ps: with ps; [
    fastapi
    uvicorn
    python-multipart
    httpx
    python-jose
    passlib
    pydantic
    pyyaml
    psutil
    numpy
    tenacity
    aiofiles
    # ML/CV dependencies
    # The following may need overlay or binary fetches if not in nixpkgs:
    torch
    torchvision
    torchaudio
    opencv4  # opencv-python is opencv4 in nixpkgs
    scipy
    tensorflow
    scikit-learn
    scikit-image
    pandas
    pillow
    pytesseract
    google-generativeai  # Check availability; may need to fetch from PyPI
    sqlalchemy
    pymongo
    kafka-python
    aiohttp
    redis # redis-py
  ];
in
{
  channel = "stable-24.05";

  packages = [
    pkgs.nodejs_20
    (pkgs.python311.override {
      packageOverrides = pythonOverlay;
    }).withPackages pythonPackages
    pkgs.git
    pkgs.curl
  ];

  idx.extensions = [
    "ms-python.python"
    "ms-python.debugpy"
    "ms-python.black-formatter"
    "ms-pyright.pyright"
    "ms-python.flake8"
    "ms-toolsai.jupyter"
    "esbenp.prettier-vscode"
    "ms-vscode.js-debug"
    "ms-vscode.vscode-json"
    "GitHub.vscode-pull-request-github"
  ];

  idx.previews = {
    enable = true;
    previews = {
      backend = {
        command = [
          "/bin/sh"
          "-c"
          "cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 9002 --reload"
        ];
        manager = "web";
      };

      frontend = {
        command = [
          "/bin/sh"
          "-c"
          "cd frontend && npm run dev -- --port 3000 --hostname 0.0.0.0"
        ];
        manager = "web";
        env = {
          NEXT_PUBLIC_API_URL = "https://9002-$WEB_HOST";
        };
      };
    };
  };

  idx.workspace = {
    onCreate = {
      setup-frontend = "cd frontend && npm install";
      # No need to set up backend venv or pip install anymore!
    };

    onStart = {
      check-environments = ''
        echo "Checking environments..."
        if [ ! -d "frontend/node_modules" ]; then
          echo "Frontend node_modules missing - run npm install"
        else
          echo "Frontend ready"
        fi
        echo "Development URLs:"
        echo "  Frontend: https://3000-$WEB_HOST"
        echo "  Backend: https://9002-$WEB_HOST"
      '';
    };
  };
}
