from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import keyring
from TM1py import TM1Service


ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT_DIR / "config" / "tm1_config.json"

DEFAULT_KEYRING_SERVICE = "OXYChem"

REQUIRED_CONFIG_KEYS = (
    "base_url",
    "user_account",
    "namespace",
)


def normalize_config_value(
    config: dict[str, Any],
    key: str,
) -> str:
    """Return a required, nonempty configuration value."""

    value = str(
        config.get(key) or ""
    ).strip()

    if not value:
        raise ValueError(
            f"TM1 connection configuration value "
            f"'{key}' is missing or empty in "
            f"{CONFIG_FILE}."
        )

    return value


def load_tm1_config() -> dict[str, Any]:
    """Load and validate the TM1 connection configuration."""

    if not CONFIG_FILE.is_file():
        raise FileNotFoundError(
            "TM1 connection configuration file "
            f"was not found: {CONFIG_FILE}"
        )

    try:
        with CONFIG_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as file:
            payload: Any = json.load(file)
    except json.JSONDecodeError as error:
        raise ValueError(
            "TM1 connection configuration contains "
            "invalid JSON: "
            f"line {error.lineno}, "
            f"column {error.colno}: "
            f"{error.msg}"
        ) from error
    except OSError as error:
        raise OSError(
            "Unable to read the TM1 connection "
            f"configuration: {CONFIG_FILE}"
        ) from error

    if not isinstance(payload, dict):
        raise TypeError(
            "TM1 connection configuration must "
            "contain a JSON object."
        )

    config = dict(payload)

    for key in REQUIRED_CONFIG_KEYS:
        normalize_config_value(
            config,
            key,
        )

    return config


def get_tm1_connection() -> TM1Service:
    """Create a configured TM1Service connection.

    The caller should use the returned connection as
    a context manager so it is closed after use.
    """

    config = load_tm1_config()

    base_url = normalize_config_value(
        config,
        "base_url",
    )
    user_account = normalize_config_value(
        config,
        "user_account",
    )
    namespace = normalize_config_value(
        config,
        "namespace",
    )

    keyring_service = str(
        config.get(
            "keyring_service",
            DEFAULT_KEYRING_SERVICE,
        )
        or DEFAULT_KEYRING_SERVICE
    ).strip()

    password = keyring.get_password(
        keyring_service,
        user_account,
    )

    if not password:
        raise ValueError(
            "No TM1 password was found in the "
            "operating-system credential store for "
            f"keyring service '{keyring_service}'."
        )

    ssl_enabled = config.get(
        "ssl",
        True,
    )

    if not isinstance(
        ssl_enabled,
        bool,
    ):
        raise TypeError(
            "TM1 configuration value 'ssl' must "
            "be true or false."
        )

    verify_enabled = config.get(
        "verify",
        True,
    )

    if not isinstance(
        verify_enabled,
        bool,
    ):
        raise TypeError(
            "TM1 configuration value 'verify' must "
            "be true or false."
        )

    print(
        "TM1 connection configuration loaded."
    )

    return TM1Service(
        base_url=base_url,
        user=user_account,
        password=password,
        namespace=namespace,
        ssl=ssl_enabled,
        verify=verify_enabled,
    )