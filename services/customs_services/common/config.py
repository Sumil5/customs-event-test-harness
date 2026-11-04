"""Service configuration, read once from environment variables."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Topics:
    inbound: str
    validated: str
    assessed: str
    rejected: str


@dataclass(frozen=True)
class Settings:
    service_name: str
    instance_id: str
    bootstrap_servers: str
    oracle_dsn: str
    oracle_user: str
    oracle_password: str
    schema_path: str
    cloudwatch_enabled: bool
    faults: str
    topics: Topics

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        return cls(
            service_name=env("SERVICE_NAME", "unknown"),
            instance_id=env("INSTANCE_ID", socket.gethostname()),
            bootstrap_servers=env("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            oracle_dsn=env("ORACLE_DSN", "localhost:1521/FREEPDB1"),
            oracle_user=env("ORACLE_USER", "customs"),
            oracle_password=env("ORACLE_PASSWORD", "customs_pw"),
            schema_path=env("SCHEMA_PATH", "schemas/goods-movement-declaration-v1.xsd"),
            cloudwatch_enabled=_flag(env("CLOUDWATCH_ENABLED", "false")),
            faults=env("FAULTS", ""),
            topics=Topics(
                inbound=env("TOPIC_INBOUND", "customs.declarations.inbound"),
                validated=env("TOPIC_VALIDATED", "customs.declarations.validated"),
                assessed=env("TOPIC_ASSESSED", "customs.declarations.assessed"),
                rejected=env("TOPIC_REJECTED", "customs.declarations.rejected"),
            ),
        )
