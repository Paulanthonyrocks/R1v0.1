from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Dict, Any
from pathlib import Path
import logging
import re

from app.dependencies import get_current_active_user
from app.config import get_current_config


router = APIRouter()
logger = logging.getLogger("app.routers.logs")


@router.get("/system-logs", response_model=List[Dict[str, Any]])
async def get_system_logs(
    log_file_name: str = "backend_main.log",
    limit: int = 100,
    offset: int = 0,
    current_user: Dict[str, Any] = Depends(
        get_current_active_user
    ),  # Protect with authentication
) -> List[Dict[str, Any]]:
    """
    Retrieves system logs from a specified log file.
    Requires authentication.
    """
    # Define an allow-list of log files that can be accessed via the API
    # This should ideally be configurable, but for now, it's hardcoded for security.
    ALLOWED_LOG_FILES = [
        "backend_main.log",
        "uvicorn_access.log",
        "uvicorn_error.log",
        "app.log",
    ]

    if log_file_name not in ALLOWED_LOG_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid log file name or not allowed.",
        )

    config = get_current_config()
    log_config = config.get("logging", {})

    # Get the base log directory from the config
    # We assume all log files are in the same directory as backend_main.log
    main_file_handler = log_config.get("handlers", {}).get("mainFileHandler", {})
    log_file_path_str = main_file_handler.get("filename")

    if not log_file_path_str:
        logger.error("Log file path not configured in mainFileHandler.")
        raise HTTPException(status_code=500, detail="Log file path not configured.")

    log_dir = Path(log_file_path_str).parent
    target_log_file = log_dir / log_file_name

    if not target_log_file.exists():
        logger.warning(f"Requested log file not found: {target_log_file}")
        raise HTTPException(
            status_code=404, detail=f"Log file '{log_file_name}' not found."
        )

    logs = []
    try:
        with open(target_log_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

            # Apply offset and limit
            start_index = offset
            end_index = min(offset + limit, len(lines))

            for i in range(start_index, end_index):
                line = lines[i].strip()
                if line:
                    # Basic parsing: attempt to extract timestamp, level, message
                    # This is a simple regex and might need to be more robust
                    match = re.match(
                        r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (?P<name>[\w.]+) - (?P<level>\w+) - (?P<message>.*)$",
                        line,
                    )
                    if match:
                        logs.append(match.groupdict())
                    else:
                        logs.append({"message": line})  # Fallback for unparsable lines
    except Exception as e:
        logger.error(f"Error reading log file {target_log_file}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error reading log file.")

    return logs


@router.get("/log-files", response_model=List[str])
async def get_available_log_files(
    current_user: Dict[str, Any] = Depends(
        get_current_active_user
    ),  # Protect with authentication
) -> List[str]:
    """
    Retrieves a list of available log files in the backend logs directory.
    Requires authentication.
    """
    config = get_current_config()
    log_config = config.get("logging", {})
    main_file_handler = log_config.get("handlers", {}).get("mainFileHandler", {})
    log_file_path_str = main_file_handler.get("filename")

    if not log_file_path_str:
        logger.error("Log file path not configured in mainFileHandler.")
        raise HTTPException(status_code=500, detail="Log file path not configured.")

    log_dir = Path(log_file_path_str).parent

    if not log_dir.is_dir():
        logger.warning(f"Log directory not found: {log_dir}")
        return []  # Return empty list if directory doesn't exist

    try:
        # List only files ending with .log
        log_files = [
            f.name for f in log_dir.iterdir() if f.is_file() and f.name.endswith(".log")
        ]
        return sorted(log_files)
    except Exception as e:
        logger.error(f"Error listing log files in {log_dir}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error listing log files.")
