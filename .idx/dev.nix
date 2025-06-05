{pkgs}:

{
  channel = "stable-24.05";

  # Keep only essential system packages - everything else goes in venvs
  packages = [
    pkgs.nodejs_20
    pkgs.python311
    pkgs.git
    pkgs.curl
  ];

  # VS Code extensions
  idx.extensions = [
    "esbenp.prettier-vscode"
    "GitHub.vscode-pull-request-github"
    "ms-pyright.pyright"
    "ms-python.debugpy"
    "ms-python.python"
    "ms-toolsai.jupyter"
    "ms-vscode.js-debug"
    "ms-vscode.vscode-json"
    "ms-python.flake8"
    "ms-python.black-formatter"
  ];

  # Preview configurations - both running from virtual environments
  idx.previews = {
    enable = true;
    previews = {
      backend = {
        command = [
          "/bin/sh"
          "-c"
          "cd backend && source venv/bin/activate && python -m uvicorn app.main:app --host 0.0.0.0 --port 9002 --reload"
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

  # Workspace lifecycle - set up both environments on create
  idx.workspace = {
    onCreate = {
      setup-frontend = "cd frontend && npm install";
      setup-backend = ''
        cd backend && 
        python -m venv venv && 
        source venv/bin/activate && 
        pip install --upgrade pip && 
        pip install -r requirements.txt
      '';
    };

    onStart = {
      check-environments = ''
        echo "Checking environments..."
        if [ ! -d "backend/venv" ]; then
          echo "Backend venv missing - will be created on first run"
        else
          echo "Backend venv exists"
          # Check if requirements.txt is newer than last install
          cd backend
          if [ -f "requirements.txt" ] && [ -f "venv/pyvenv.cfg" ]; then
            if [ "requirements.txt" -nt "venv/pyvenv.cfg" ]; then
              echo "requirements.txt updated - reinstalling packages..."
              source venv/bin/activate && pip install -r requirements.txt
            else
              echo "Backend packages up to date"
            fi
          fi
          cd ..
        fi
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
