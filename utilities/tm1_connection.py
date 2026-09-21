import json
import keyring
from pathlib import Path
from TM1py import TM1Service


config_file = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "tm1_config.json"
)


def get_tm1_connection():

    if not config_file.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_file}"
        )

    with open(config_file, "r") as f:
        config = json.load(f)

    if not config:
        raise ValueError(
            "TM1 connection config file is empty or invalid"
        )

    required_keys = [
        "base_url",
        "user_account",
        "namespace"
    ]

    for key in required_keys:
        if key not in config:
            raise KeyError(
                f"Missing '{key}' in {config_file}"
            )

    password = keyring.get_password(
        "OXYChem",
        config["user_account"]
    )

    if not password:
        raise ValueError(
            f"No password found in keyring for "
            f"{config['user_account']}"
        )

    print("Base URL:", config["base_url"])
    print("Namespace:", config["namespace"])
    print("User:", config["user_account"])
    print("Password Found:", password is not None)

    return TM1Service(
        base_url=config["base_url"],
        user=config["user_account"],
        password=password,
        ssl=True,
        namespace=config["namespace"]
    )

