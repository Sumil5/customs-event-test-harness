"""Harness settings. Every value can be overridden with an environment variable
of the same name (e.g. KAFKA_BOOTSTRAP_SERVERS, ORACLE_DSN, SLA_SECONDS)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kafka_bootstrap_servers: str = "localhost:9092"
    oracle_dsn: str = "localhost:1521/FREEPDB1"
    oracle_user: str = "customs"
    oracle_password: str = "customs_pw"

    topic_inbound: str = "customs.declarations.inbound"
    topic_validated: str = "customs.declarations.validated"
    topic_assessed: str = "customs.declarations.assessed"
    topic_rejected: str = "customs.declarations.rejected"

    schema_path: Path = PROJECT_ROOT / "schemas" / "goods-movement-declaration-v1.xsd"
    mapping_path: Path = PROJECT_ROOT / "config" / "mappings" / "goods_movement_declaration.yaml"
    reports_dir: Path = PROJECT_ROOT / "reports"

    sla_seconds: float = 13.0

    # Where a journey ends: the row's commit timestamp, found by correlation ID.
    trace_table: str = "DECLARATION"
    trace_correlation_column: str = "CORRELATION_ID"
    trace_timestamp_column: str = "PERSISTED_AT"

    # CloudWatch Logs: the local moto emulator by default. Set AWS_ENDPOINT_URL="" for real AWS.
    aws_endpoint_url: str | None = "http://localhost:5050"
    aws_default_region: str = "eu-west-2"
    cloudwatch_log_groups: str = "/customs/intake,/customs/assessment,/customs/persistence"

    @property
    def stage_topics(self) -> dict[str, str]:
        """Pipeline stages in order, mapped to the topic whose records mark them."""
        return {
            "submitted": self.topic_inbound,
            "validated": self.topic_validated,
            "assessed": self.topic_assessed,
            "rejected": self.topic_rejected,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
