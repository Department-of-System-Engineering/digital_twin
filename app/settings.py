from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr
from sqlalchemy import URL


class Settings(BaseSettings):
    # Existing deployments may still provide complete SQLAlchemy URLs.
    DATABASE_URL: str | None = None
    ASYNC_DATABASE_URL: str | None = None

    # Components avoid manual URL-encoding of database passwords.
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str | None = None
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: SecretStr | None = None

    CMMS_BASE_URL: str
    CMMS_TOKEN: SecretStr

    DC_BASE_URL: str | None = None
    DC_API_KEY: SecretStr | None = None
    PREDICTION_CONFIG_PATH: str = "./config/prediction.toml"
    DATACOLLECTOR_CONFIG_PATH: str = "./config/datacollector.toml"
    DATACOLLECTOR_REJECTED_LOG_PATH: str = "./logs/datacollector_rejected.log"
    SEED_WORKBOOK_PATH: str = "./db/init/data/db_init_data.xlsx"

    INBOUND_API_KEY: SecretStr
    MAPPING_ADMIN_API_KEY: SecretStr | None = None

    DASHBOARD_LIVE_TOLERANCE_SECONDS: float = 5.0
    DASHBOARD_WS_POLL_INTERVAL_SECONDS: float = 0.1
    DASHBOARD_MAX_CHART_POINTS: int = Field(default=5000, gt=0)

    DATA_DIR: str = "./app/maintenance/prediction_out"  # helyi könyvtár is lehet

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def sync_database_url(self) -> str | URL:
        if self.DATABASE_URL:
            return self.DATABASE_URL

        return self._component_database_url(
            drivername="postgresql+psycopg"
        )

    @property
    def async_database_url(self) -> str | URL:
        if self.ASYNC_DATABASE_URL:
            return self.ASYNC_DATABASE_URL

        return self._component_database_url(
            drivername="postgresql+asyncpg"
        )

    def _component_database_url(self, drivername: str) -> URL:
        missing = [
            name
            for name, value in (
                ("POSTGRES_DB", self.POSTGRES_DB),
                ("POSTGRES_USER", self.POSTGRES_USER),
                ("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD),
            )
            if value is None
        ]

        if missing:
            raise ValueError(
                "Set DATABASE_URL/ASYNC_DATABASE_URL or provide: " + ", ".join(missing))

        assert self.POSTGRES_PASSWORD is not None

        return URL.create(
            drivername=drivername,
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD.get_secret_value(),
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        )


settings = Settings()
