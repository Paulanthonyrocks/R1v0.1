# .idx/dev.nix
{pkgs}: {
  channel = "stable-24.05";

  packages = [
    pkgs.nodejs_20
    pkgs.tesseract

    # Define the Python environment with packages managed by Nix
    (pkgs.python311.withPackages (ps: [
      # --- Core FastAPI ---
      ps.fastapi
      ps.uvicorn
      ps.python-multipart

      # --- Configuration & Utilities ---
      ps.pyyaml
      ps.psutil
      ps.numpy
      ps.tenacity
      ps.aiofiles

      # --- ML/CV Dependencies (Managed by Nix where possible) ---
      ps.pytorch
      ps.torchvision
      ps.torchaudio
      # ultralytics removed - will be installed via pip
      ps.opencv4 # Using nixpkgs opencv
      ps.scipy
      # filterpy removed - will be installed via pip

      # --- OCR Dependencies ---
      ps.pillow
      ps.pytesseract
      # google-generativeai removed - will be installed via pip

      # --- Utilities ---
      ps.pip # Needed for installing extra requirements
      # ps.wheel
      # ps.setuptools

    ])) # End of python311.withPackages
  ]; # End of packages

  idx.extensions = [
    # ... (keep all your extensions)
    "amazonwebservices.amazon-q-vscode"
    "Angular.ng-template"
    "anysphere.pyright"
    "bradlc.vscode-tailwindcss"
    "cweijan.dbclient-jdbc"
    "cweijan.vscode-mysql-client2"
    "dbaeumer.vscode-eslint"
    "eamodio.gitlens"
    "EditorConfig.EditorConfig"
    "esbenp.prettier-vscode"
    "GitHub.vscode-pull-request-github"
    "golang.go"
    "ms-azuretools.vscode-docker"
    "ms-pyright.pyright"
    "ms-python.debugpy"
    "ms-python.python"
    "ms-toolsai.jupyter"
    "ms-toolsai.jupyter-keymap"
    "ms-toolsai.jupyter-renderers"
    "ms-toolsai.vscode-jupyter-cell-tags"
    "ms-toolsai.vscode-jupyter-slideshow"
    "ms-vscode.js-debug"
    "PKief.material-icon-theme"
    "rangav.vscode-thunder-client"
    "redhat.java"
    "redhat.vscode-yaml"
    "rust-lang.rust-analyzer"
    "saoudrizwan.claude-dev"
    "vscjava.vscode-gradle"
    "vscjava.vscode-java-debug"
    "vscjava.vscode-java-dependency"
    "vscjava.vscode-java-pack"
    "vscjava.vscode-java-test"
    "vscjava.vscode-maven"
   ]; # End of idx.extensions

  idx.previews = {
    enable = true;
    previews = {

      backend = {
        command = [
          "uvicorn"
          "app.main:app"
          "--host"
          "0.0.0.0"
          "--port"
          "9002"
          "--reload"
          "--app-dir"
          "backend/"
        ];
        manager = "web";
      }; # ; needed

      frontend = {
        command = [
          "/bin/sh"
          "-c"
          "cd frontend && npm run dev -- --port $PORT --hostname 0.0.0.0"
        ];
        manager = "web";
        env = {
          NEXT_PUBLIC_API_URL = "http://localhost:9002";
        };
      };

    };
  };

  # Workspace lifecycle hooks
  idx.workspace = {
    # Commands to run once when the workspace is created or rebuilt
    onCreate = {
      # Install frontend dependencies
      npm-install = "cd frontend && npm install";
      # Install Python dependencies MISSING from Nixpkgs using pip
      pip-install-extras = "cd backend && pip install --no-cache-dir -r requirements-extra.txt";
    }; # ; needed

    # Commands to run every time the workspace starts
    onStart = {
      log-start = "echo Nix environment ready. Starting previews...";
      # Check frontend config and a key backend file / requirements file
      check-files = "ls -l frontend/package.json backend/app/main.py backend/requirements-extra.txt || true";
    };
  }; # End of idx.workspace set

} # End of main function set