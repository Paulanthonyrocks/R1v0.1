import yaml
from pathlib import Path
import logging
from typing import Dict, Any


class ConfigError(Exception):
    """Custom exception for configuration errors."""

    pass


class _DuplicateKeyError(yaml.YAMLError):
    pass


def _construct_mapping_no_dups(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise _DuplicateKeyError(
                f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


class StrictYamlLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently
    letting the last one win."""


StrictYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_no_dups
)


def load_yaml_strict(stream):
    """yaml.safe_load equivalent that raises ConfigError on duplicate keys."""
    try:
        return yaml.load(stream, Loader=StrictYamlLoader)
    except _DuplicateKeyError as e:
        raise ConfigError(f"Duplicate key in YAML: {e}") from e


def _check_coupled_keys(config: Dict[str, Any]) -> None:
    """Fail loudly when redundant keys that must agree have drifted apart.

    Several FRs are governed by multiple YAML keys read by different
    consumers; when they disagree, behavior silently follows whichever
    reader fires first (e.g. result_processor dedups wire fps to the
    minimum of top-level fps / video_output.fps / target_fps).
    """
    problems = []
    fps_keys = {
        "fps": config.get("fps"),
        "video_output.fps": config.get("video_output", {}).get("fps"),
        "video_processing.target_fps": config.get("video_processing", {}).get("target_fps"),
    }
    present = {k: v for k, v in fps_keys.items() if v is not None}
    if len(present) > 1 and len(set(present.values())) > 1:
        problems.append(f"frame-rate keys disagree: {present}")
    stopped_keys = {
        "stopped_speed_threshold_kmh": config.get("stopped_speed_threshold_kmh"),
        "behavior_analysis.stopped_speed_threshold_kmh": config.get("behavior_analysis", {}).get("stopped_speed_threshold_kmh"),
        "collision_detection.stopped_speed_kmh": config.get("collision_detection", {}).get("stopped_speed_kmh"),
    }
    present = {k: v for k, v in stopped_keys.items() if v is not None}
    if len(present) > 1 and len(set(present.values())) > 1:
        problems.append(f"stopped-speed keys disagree: {present}")
    if problems:
        raise ConfigError("Coupled config keys drifted apart: " + "; ".join(problems))


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
        "log_path": "./logs/backend_main.log",  # Default log path for main application
    },
    "github": {
        "token": None,  # Replace with your GitHub token
        "username": None,  # Replace with your GitHub username
        "repositories": [],  # list of repositories to process by default
    },
    "database": {
        "url": "sqlite:///./app.db",  # Default SQLite database
    },
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
                user_config = load_yaml_strict(f)
            if user_config:  # Check if the user_config is not None
                config = merge_dicts(config, user_config)
            _check_coupled_keys(config)
        except ConfigError:
            raise
        except yaml.YAMLError as e:
            logging.error(f"Error parsing YAML file: {e}")
            raise ConfigError(f"Error parsing YAML file: {e}") from e
        except (
            Exception
        ) as e:  # Catch any other unexpected errors during file processing
            logging.error(f"Error loading configuration file: {e}")
            raise ConfigError(f"Error loading configuration file: {e}") from e
    else:
        logging.warning(
            f"Configuration file not found at {config_file}. Using default settings."
        )
    return config
