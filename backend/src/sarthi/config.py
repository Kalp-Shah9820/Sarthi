from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SARTHI_", env_file=BACKEND_ROOT / ".env", extra="ignore")

    db_path: str = "data/sarthi.db"
    llm_base_url: str = "http://localhost:1234/v1"
    llm_model: str = "qwen/qwen3-4b-2507"
    llm_enabled: bool = True
    llm_timeout_s: float = 45.0
    llm_reasoning_headroom: int = 0
    weather_enabled: bool = True
    mc_paths: int = 2000
    seed: int = 20261005
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # decision constants (overridable by the active strategy policy)
    review_period_days: int = 7
    stockout_alert_prob: float = 0.15      # PDF: alert when P(stockout) > 15 %
    critical_prob: float = 0.60            # UI: "critical" above 60 %
    overstock_cover_days: int = 30         # PDF: Ghost when days of cover > 30
    auto_confidence: float = 85.0          # UI: green confidence bar from 85 %
    auto_max_order_value: float = 50_000.0
    goodwill_factor: float = 0.20          # lost-customer penalty as a share of lost margin
    monthly_budget: float = 500_000.0

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else BACKEND_ROOT / p

    @property
    def feeds_dir(self) -> Path:
        """Simulated external feeds live next to the database."""
        return self.db_file.parent / "feeds"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
