"""Test harness for an event-driven customs pipeline.

* generator      - builds valid (and deliberately invalid) XML declarations and publishes them to Kafka
* reconciliation - proves every Kafka message landed in Oracle, field by field
* tracing        - follows each correlation ID end to end and checks the latency SLA
"""

__version__ = "1.0.0"
