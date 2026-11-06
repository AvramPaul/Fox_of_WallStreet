"""Configurare centrală: încarcă .env și expune constantele proiectului."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

CACHE_DIR = ROOT / ".cache"
RUNS_DIR = ROOT / "runs"
REPORT_PATH = ROOT / "raport_sateliti.md"

VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # LLM
    model: str = field(default_factory=lambda: os.getenv("MODEL") or "claude-opus-5-5")
    effort: str = field(default_factory=lambda: os.getenv("EFFORT") or "high")
    max_tokens: int = 64000
    max_iterations: int = field(default_factory=lambda: _env_int("MAX_ITERATIONS", 40))

    # Bugete per tool (gardieni anti-buclă)
    max_screener_calls: int = 10
    max_inspector_calls: int = 12
    max_osint_calls: int = 8

    # Dimensiunea maximă a unui tool_result (caractere)
    max_tool_output_chars: int = 6000

    # Rețea
    http_timeout: float = 10.0
    retry_attempts: int = 4
    # Identificator onest pentru cererile proprii (HN, GitHub, Reddit) — nu ne dăm drept browser.
    user_agent: str = "ParanoidWolf-SatelliteHunter/0.1 (personal investment research script)"
    github_token: str | None = field(default_factory=lambda: os.getenv("GITHUB_TOKEN") or None)

    # Intervale minime între cereri, per sursă (secunde)
    min_interval: dict = field(default_factory=lambda: {
        "finviz": 1.5,
        "yfinance": 0.5,
        "ddg": 2.0,
        "hn": 0.5,
        "github": 6.5,  # 10 req/min fără token
        "reddit": 2.0,
    })

    # TTL cache pe disc (secunde)
    cache_ttl: dict = field(default_factory=lambda: {
        "finviz": 6 * 3600,
        "yfinance": 24 * 3600,
        "osint": 12 * 3600,
    })

    def __post_init__(self):
        if self.effort not in VALID_EFFORTS:
            raise ValueError(f"EFFORT invalid: {self.effort!r}; valori permise: {VALID_EFFORTS}")


def load(**overrides) -> Settings:
    """Construiește Settings din .env, cu suprascrieri opționale (ex. din CLI)."""
    s = Settings()
    if overrides:
        from dataclasses import replace
        s = replace(s, **{k: v for k, v in overrides.items() if v is not None})
    CACHE_DIR.mkdir(exist_ok=True)
    RUNS_DIR.mkdir(exist_ok=True)
    return s
