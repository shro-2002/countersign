"""Settings loaded from the repo-root .env. Refuses any PayPal host other than the sandbox."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
SANDBOX_BASE = "https://api-m.sandbox.paypal.com"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    paypal_client_id: str
    paypal_client_secret: str
    paypal_base_url: str
    db_url: str
    gemini_api_key: str | None
    operator_token: str = ""
    llm_request_budget: int = 12
    demo_mode: bool = False  # judge mode: see countersign/demo.py


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():  # hosted: settings come from the environment
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = {**_read_env(ENV_PATH), **{k: v for k, v in os.environ.items() if k.startswith(("PAYPAL_", "COUNTERSIGN_",
                                                                                      "GEMINI_"))}}
    missing = [k for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_BASE_URL", "COUNTERSIGN_DB_URL",
                           "COUNTERSIGN_OPERATOR_TOKEN")
               if not env.get(k)]
    if missing:
        raise ConfigError(f"missing settings: {', '.join(missing)} (set them in .env, see .env.example)")
    if env["PAYPAL_BASE_URL"].rstrip("/") != SANDBOX_BASE or urlparse(env["PAYPAL_BASE_URL"]).scheme != "https":
        raise ConfigError(f"PAYPAL_BASE_URL must be {SANDBOX_BASE} (sandbox only)")
    return Settings(
        paypal_client_id=env["PAYPAL_CLIENT_ID"],
        paypal_client_secret=env["PAYPAL_CLIENT_SECRET"],
        paypal_base_url=SANDBOX_BASE,
        db_url=env["COUNTERSIGN_DB_URL"],
        gemini_api_key=env.get("GEMINI_API_KEY") or None,
        operator_token=env["COUNTERSIGN_OPERATOR_TOKEN"],
        llm_request_budget=int(env.get("COUNTERSIGN_LLM_BUDGET", "12")),
        demo_mode=env.get("COUNTERSIGN_DEMO_MODE") == "judge",
    )
