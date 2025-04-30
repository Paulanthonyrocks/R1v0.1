# .idx/dev.nix
{pkgs}: {
  channel = "stable-24.05"; # Or your preferred channel

  packages = [
    pkgs.nodejs_20
    pkgs.tesseract
    (pkgs.python311.withPackages (ps: [
      ps.fastapi
      ps.uvicorn
      ps.pytesseract
      ps.numpy
      ps.psutil
      # --- Add other Python packages like ps.requests,  etc. ---
      ps.pip 
    ])) # End of python311.withPackages
  ]; # End of packages

  idx.extensions = [ "amazonwebservices.amazon-q-vscode" "Angular.ng-template" "anysphere.pyright" "bradlc.vscode-tailwindcss" "cweijan.dbclient-jdbc" "cweijan.vscode-mysql-client2" "dbaeumer.vscode-eslint" "eamodio.gitlens" "EditorConfig.EditorConfig" "esbenp.prettier-vscode" "GitHub.vscode-pull-request-github" "golang.go" "ms-azuretools.vscode-docker" "ms-pyright.pyright" "ms-python.debugpy" "ms-python.python" "ms-toolsai.jupyter" "ms-toolsai.jupyter-keymap" "ms-toolsai.jupyter-renderers" "ms-toolsai.vscode-jupyter-cell-tags" "ms-toolsai.vscode-jupyter-slideshow" "ms-vscode.js-debug" "PKief.material-icon-theme" "rangav.vscode-thunder-client" "redhat.java" "redhat.vscode-yaml" "rust-lang.rust-analyzer" "saoudrizwan.claude-dev" "vscjava.vscode-gradle" "vscjava.vscode-java-debug" "vscjava.vscode-java-dependency" "vscjava.vscode-java-pack" "vscjava.vscode-java-test" "vscjava.vscode-maven"];

  idx.previews = {
    enable = true;
    previews = { # Start of inner 'previews' set

      backend = { # Start of 'backend' set
        command = [
          "uvicorn"
          "backend.app.main:app" # Path to your FastAPI app instance
          "--host" "0.0.0.0"    # Listen on all interfaces
          "--port" "$PORT"     # Use the port assigned by IDX
          "--reload" # Optional reload flag
        ]; # <--- Correctly closed list, semicolon needed after it.
        manager = "web";
      }; # <--- Semicolon needed after 'backend' set definition

      frontend = { # Start of 'frontend' set
        command = [
          "/bin/sh"
          "-c"
          # Command string for shell
          "cd frontend && npm run dev -- --port $PORT --hostname 0.0.0.0"
        ]; # <--- Correctly closed list, semicolon needed after it.
        manager = "web";
        env = { # Start of 'env' set
          NEXT_PUBLIC_API_URL = "http://localhost:9002"; # Adjust if necessary
          # Add other env vars here if needed, end with semicolon
        }; # <--- Correctly closed set, semicolon needed after it.
      }; # <--- Semicolon needed after 'frontend' set definition (unless it's the ABSOLUTE last item in the 'previews' set)

    }; # <--- Correctly closed inner 'previews' set
  }; # <--- Correctly closed outer 'idx.previews' set

  idx.workspace = {
    onCreate = {
      npm-install = "cd frontend && npm install || true";
      pip-install = "cd backend && pip install --user -r requirements.txt || true"; 
    }; # <--- Correctly closed set, semicolon needed after it.
    onStart = {
      log-start = "echo Nix environment ready. Starting previews...";
      check-files = "ls -l frontend/package.json backend/app/main.py || true";
    }; # <--- Correctly closed set (no semicolon needed if it's the last item in idx.workspace)
  }; # <--- Correctly closed outer 'idx.workspace' set
} # <--- Correctly closed main function set