#!/usr/bin/env bash
# Creates the pipeline topics (idempotent). Runs once as the kafka-init container.
set -euo pipefail

BOOTSTRAP="${BOOTSTRAP:-kafka:19092}"
PARTITIONS="${PARTITIONS:-3}"
TOPICS=(
  customs.declarations.inbound     # raw submissions (payload generator publishes here)
  customs.declarations.validated   # passed schema + business-rule validation
  customs.declarations.assessed    # risk score and lane added
  customs.declarations.rejected    # dead-letter topic: original payload + rejection headers
)

for topic in "${TOPICS[@]}"; do
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" \
    --create --if-not-exists --topic "$topic" \
    --partitions "$PARTITIONS" --replication-factor 1 \
    --config retention.ms=604800000
done

/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --list
