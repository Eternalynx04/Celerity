"""Read configuration once per app instance; experiments require a restart."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CELERITY_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://celerity:celerity_dev_only@127.0.0.1:55432/celerity"
    shipping_url: str = "http://127.0.0.1:8001"
    enable_n_plus_one: bool = False
    enable_missing_index: bool = False
    enable_redundant_shipping: bool = False
    shipping_delay_ms: int = Field(default=40, ge=0, le=2000)

    @property
    def shipping_calls(self) -> int:
        return 3 if self.enable_redundant_shipping else 1

    def faults(self) -> dict[str, bool]:
        return {
            "n_plus_one": self.enable_n_plus_one,
            "missing_index": self.enable_missing_index,
            "redundant_shipping": self.enable_redundant_shipping,
        }
