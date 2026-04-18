from __future__ import annotations

import yaml


_REQUIRED_KEYS = [
    "hmm",
    "lgbm",
    "features",
    "strategy",
    "backtest",
    "risk",
    "pdt",
    "session",
    "assets",
    "monitoring",
]


def load_config(path: str = "config/settings.yaml") -> dict:
    """Read settings.yaml and return the full config dict."""
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if cfg is None:
        cfg = {}
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> None:
    """Raise ValueError if any required top-level key is missing."""
    missing = [k for k in _REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(
            f"config/settings.yaml is missing required top-level key(s): {missing}"
        )


def get_asset_class(symbol: str) -> str:
    """Return 'crypto' if symbol contains '/', otherwise 'equity'."""
    return "crypto" if "/" in symbol else "equity"
