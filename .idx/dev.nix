{pkgs}:

let
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
    ultralytics
    opencv4  # opencv-python is opencv4 in nixpkgs
    scipy
    filterpy
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
    firebase-admin
    aiohttp
    redis
  ];
in
{
  channel = "stable-24.05";

  packages = [
    pkgs.nodejs_20
    (pkgs.python311.withPackages pythonPackages)
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
