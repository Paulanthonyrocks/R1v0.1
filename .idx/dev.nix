# .idx/dev.nix
{pkgs}: {
  channel = "stable-24.05";
  
  packages = [
    pkgs.nodejs_20
    pkgs.tesseract
    # Add the base OpenCV library here if needed system-wide,
    pkgs.opencv4
    pkgs.docker-compose
    pkgs.sudo

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

      # --- Kafka ---
      ps.kafka-python

      # --- Database ---
      ps.pymongo

      # --- ML/CV Dependencies (Managed by Nix where possible) ---
      ps.pytorch
      ps.torchvision
      ps.torchaudio
      # ultralytics removed - will be installed via pip
      ps.opencv4 # <--- Python bindings for OpenCV provided by Nix
      ps.scipy
      # filterpy removed - will be installed via pip

      # --- OCR Dependencies ---
      ps.pillow
      ps.pytesseract
      ps.google-generativeai # <--- Managed by Nix

      # --- Utilities ---
      ps.pip # Needed for installing extra requirements from requirements.txt
      # ps.wheel # Usually not needed unless building wheels directly
      # ps.setuptools # Usually pulled in as a dependency if needed

    ])) # End of python311.withPackages
  ]; # End of packages

  idx.extensions = [
    # ... (keep all your extensions) ...
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
        # Use absolute path for python from the Nix env for clarity
        # command = ["python", "-m", "uvicorn", ...] might be more robust
        command = [
          "/bin/sh"
          "-c"
          "cd backend && uvicorn app.main:app --host 0.0.0.0 --port 9002 --reload"
        ];
        manager = "web";
        # Ensure the backend waits for the pip install potentially
        # startupProbe = { # Optional: Add a probe if startup takes long after install
        #  httpGet = {
        #    path = "/docs"; # Or some health check endpoint
        #    port = 9002;
        #  };
        #  initialDelaySeconds = 15; # Wait after pip install might finish
        #  periodSeconds = 5;
        #};
      }; # end backend preview

      frontend = {
        command = [
          "/bin/sh"
          "-c"
          "cd frontend && npm run dev -- --port 3000 --hostname 0.0.0.0"
        ];
        manager = "web";
        env = {
          # This assumes the backend is accessible via localhost from the frontend container
          NEXT_PUBLIC_API_URL = "{https://localhost:3000}"; # Use IDX variable interpolation
        };
      }; # end frontend preview

    };
  }; # end idx.previews

  # Workspace lifecycle hooks
  idx.workspace = {
    # Commands to run once when the workspace is created or rebuilt
    onCreate = {
      # Install frontend dependencies
      npm-install = "cd frontend && npm install";
      # Install ONLY Python dependencies NOT managed by Nix
      pip-install = "cd backend && pip install --no-cache-dir -r requirements.txt";
    }; # end onCreate

    # Commands to run every time the workspace starts
    onStart = {
      log-start = "echo Nix environment ready. Starting previews...";
      # Check files again
      check-files = "ls -l frontend/package.json backend/app/main.py backend/requirements.txt || true";
    }; # end onStart
  }; # End of idx.workspace

} # End of main function