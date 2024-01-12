"""Handy utility functions"""

import json
import platform
import logging
import os.path
from typing import Any, Optional

logger = logging.getLogger(__package__)

# Flag so application code can detect if within a pytest run - only use if really needed
# See: https://pytest.org/en/7.4.x/example/simple.html#detect-if-running-from-within-a-pytest-run
_called_from_test = False


#
# Functions to check which OS is being used
def is_mac() -> bool:
    """Return true if running on Mac"""
    return _is_system("Darwin")


def is_windows() -> bool:
    """Return true if running on Windows"""
    return _is_system("Windows")


def is_x11() -> bool:
    """Return true if running on Linux"""
    return _is_system("Linux")


# mypy: disable-error-code="attr-defined"
def _is_system(system: str) -> bool:
    """Return true if running on given system

    Args:
        system: Name of system to check against
    """
    try:
        return _is_system.system == system
    except AttributeError:
        _is_system.system = platform.system()
        if _is_system.system not in ["Darwin", "Linux", "Windows"]:
            raise Exception("Unknown windowing system")
        return _is_system.system == system


def load_dict_from_json(filename: str) -> Optional[dict[str, Any]]:
    """If file exists, attempt to load into dict.

    Args:
        filename: Name of JSON file to load.

    Returns:
        Dictionary if loaded successfully, or None.
    """
    if os.path.isfile(filename):
        with open(filename, "r") as fp:
            try:
                return json.load(fp)
            except json.decoder.JSONDecodeError as exc:
                logger.error(f"Unable to load {filename}\n" + str(exc))
    return None
