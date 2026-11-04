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

# LogAppendTime: the broker stamps each record when it accepts it, so every stage
# timestamp the tracer uses comes from one clock - immune to client clock skew
# (e.g. a Mac host vs the Docker VM) and to client start-up delays.
for topic in "${TOPICS[@]}"; do
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" \
    --create --if-not-exists --topic "$topic" \
    --partitions "$PARTITIONS" --replication-factor 1 \
    --config retention.ms=604800000 \
    --config message.timestamp.type=LogAppendTime
  # Topics created before this setting existed get it too.
  /opt/kafka/bin/kafka-configs.sh --bootstrap-server "$BOOTSTRAP" \
    --alter --entity-type topics --entity-name "$topic" \
    --add-config message.timestamp.type=LogAppendTime
done

/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --list
