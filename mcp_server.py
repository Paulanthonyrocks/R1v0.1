import logging
from mcp.server.fastmcp import FastMCP
import os
import subprocess
import uvicorn
from typing import List

# Configure logging to write to a file
logging.basicConfig(
    filename='mcp_server.log',
    filemode='w', # 'w' overwrites the log file on each start
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

app = FastMCP()

@app.tool()
def read_file(path: str) -> str:
    """Reads the contents of a file."""
    logging.info(f"Executing read_file on: {path}")
    try:
        with open(path, "r", encoding='utf-8') as f:
            content = f.read()
        logging.info(f"Successfully read file: {path}")
        return content
    except Exception as e:
        logging.error(f"Error reading file {path}: {e}")
        return f"Error: {str(e)}"

@app.tool()
def list_directory(path: str) -> list[str]:
    """Lists the contents of a directory."""
    logging.info(f"Executing list_directory on: {path}")
    try:
        entries = os.listdir(path)
        logging.info(f"Successfully listed directory: {path}")
        return entries
    except Exception as e:
        logging.error(f"Error listing directory {path}: {e}")
        return [f"Error: {str(e)}"]

@app.tool()
def get_process_info_by_port(ports: list[int]) -> list[dict]:
    """
    Retrieves information about processes listening on specified ports.
    For Windows, it uses 'netstat -ano' and 'tasklist'.
    """
    logging.info(f"Executing get_process_info_by_port for ports: {ports}")
    process_info = []
    try:
        # Get all listening ports and PIDs
        netstat_output = subprocess.check_output(["netstat", "-ano"], universal_newlines=True)
        
        pid_to_port = {}
        for line in netstat_output.splitlines():
            parts = line.strip().split()
            if len(parts) > 4 and parts[0] == "TCP" and parts[3] == "LISTENING":
                try:
                    local_address = parts[1]
                    port = int(local_address.split(':')[-1])
                    pid = int(parts[4])
                    if port in ports:
                        pid_to_port[pid] = port
                except ValueError:
                    continue

        for pid, port in pid_to_port.items():
            try:
                tasklist_output = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}"], universal_newlines=True)
                process_name = "N/A"
                for line in tasklist_output.splitlines():
                    if str(pid) in line:
                        # Extract process name, usually the first part of the line
                        process_name = line.split(".exe")[0].strip() + ".exe"
                        break
                process_info.append({"port": port, "pid": pid, "process_name": process_name})
            except Exception as e:
                logging.warning(f"Could not get tasklist info for PID {pid}: {e}")
                process_info.append({"port": port, "pid": pid, "process_name": "Error retrieving name"})

        logging.info(f"Successfully retrieved process info: {process_info}")
        return process_info
    except Exception as e:
        logging.error(f"Error in get_process_info_by_port: {e}")
        return [{"error": str(e)}]

if __name__ == "__main__":
    logging.info("Starting MCP server on port 8001")
    # Uvicorn's logs will also be captured by the logging configuration
    uvicorn.run(app, host="0.0.0.0", port=8001)
