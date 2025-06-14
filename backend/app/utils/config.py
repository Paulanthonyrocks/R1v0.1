import yaml
from pathlib import Path
import logging
from typing import Dict, Any

class ConfigError(Exception):
    """Custom exception for configuration errors."""
    pass

DEFAULT_CONFIG: Dict[str, Any] = {
    "llm": {
        "mode": "mock",
        "model": "gemini-pro",
        "temperature": 0.7,
        "max_output_tokens": 1024,
    },
    "logging": {
        "level": "INFO",
        "file": "app.log",
    },
    "github": {
        "token": None,  # Replace with your GitHub token
        "username": None, # Replace with your GitHub username
        "repositories": [], # list of repositories to process by default
    },
    "database": {
        "url": "sqlite:///./app.db", # Default SQLite database
    }
}

def merge_dicts(source: Dict[Any, Any], destination: Dict[Any, Any]) -> Dict[Any, Any]:
    """
    Recursively merges two dictionaries.
    Args:
        source: The source dictionary to merge from.
        destination: The destination dictionary to merge into.
    Returns:
        The merged dictionary.
    """
    for key, value in source.items():
        if isinstance(value, dict):
            # get node or create one
            node = destination.setdefault(key, {})
            merge_dicts(value, node)
        else:
            destination[key] = value
    return destination

def load_config(config_file: Path = Path("config.yaml")) -> Dict[str, Any]:
    """
    Loads the YAML configuration file, merging it with default settings.
    Args:
        config_file: The path to the configuration file.
    Returns:
        A dictionary containing the application configuration.
    Raises:
        ConfigError: If the configuration file is not found or is invalid.
    """
    config = DEFAULT_CONFIG.copy()
    if config_file.exists():
        try:
            with open(config_file, "r") as f:
                user_config = yaml.safe_load(f)
            if user_config: # Check if the user_config is not None
                config = merge_dicts(user_config, config)
        except yaml.YAMLError as e:
            logging.error(f"Error parsing YAML file: {e}")
            raise ConfigError(f"Error parsing YAML file: {e}") from e
        except Exception as e: # Catch any other unexpected errors during file processing
            logging.error(f"Error loading configuration file: {e}")
            raise ConfigError(f"Error loading configuration file: {e}") from e
    else:
        logging.warning(f"Configuration file not found at {config_file}. Using default settings.")
    return config
