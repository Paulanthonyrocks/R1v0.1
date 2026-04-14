from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from app.database import get_database_manager
from app.database.manager import DatabaseManager
import json
from datetime import datetime, timezone

router = APIRouter()

def map_severity(action: str) -> str:
    action_up = action.upper()
    if any(keyword in action_up for keyword in ["DELETE", "FAIL", "ERROR", "UNAUTHORIZED", "CRITICAL"]):
        return "High"
    if any(keyword in action_up for keyword in ["POST", "PUT", "PATCH", "UPDATE"]):
        return "Medium"
    return "Low"

@router.get("/", response_model=List[Dict[str, Any]])
async def get_system_logs(
    limit: int = 100, 
    offset: int = 0, 
    db: DatabaseManager = Depends(get_database_manager)
):
    \"\"\"
    Fetch system audit logs for the telemetry page.
    \"\"\"
    try:
        # Fetch from audit_log table
        sql = "SELECT id, user_id, action, resource_type, resource_id, details, ip_address, timestamp FROM audit_log ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params = (limit, offset)
        
        # Execute read
        rows = await db._execute_read(sql, params)
        
        logs = []
        for row in rows:
            # SQLite rows are often tuples or dicts depending on the manager implementation
            # Assuming row is a dict here based on typical FastAPI/SQLAlchemy setups or custom manager
            # If row is a tuple, we access by index. Let's check the manager.
            
            # Based on _execute_read typically returning list of dicts or tuples:
            # If it's a tuple: (id, user_id, action, resource_type, resource_id, details, ip_address, timestamp)
            if isinstance(row, tuple):
                r_id, r_user, r_action, r_type, r_res_id, r_details, r_ip, r_ts = row
            else:
                r_id = row.get('id')
                r_user = row.get('user_id')
                r_action = row.get('action')
                r_type = row.get('resource_type')
                r_res_id = row.get('resource_id')
                r_details = row.get('details')
                r_ip = row.get('ip_address')
                r_ts = row.get('timestamp')

            # Convert timestamp (REAL) to ISO string
            ts_iso = datetime.fromtimestamp(r_ts, tz=timezone.utc).isoformat() if r_ts else datetime.now(timezone.utc).isoformat()
            
            # Clean up details
            desc = r_details if r_details else ""
            try:
                parsed_details = json.loads(r_details) if r_details else None
                if parsed_details:
                    desc = json.dumps(parsed_details, indent=2)
            except:
                pass

            logs.append({
                "id": r_id,
                "title": r_action,
                "description": desc,
                "timestamp": ts_iso,
                "type": r_type or "SYSTEM",
                "severity": map_severity(r_action),
                "source": r_ip or r_user or "unknown"
            })
            
        return logs
    except Exception as e:
        # Log the error and raise HTTP 500
        import logging
        logger = logging.getLogger("main")
        logger.error(f"Failed to fetch system logs: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
