"""Simulated customs services: the system under test for the harness.

Three small Kafka consumers form the pipeline:

    inbound --> intake --> validated --> assessment --> assessed --> persistence --> Oracle
                  \\--------------------------> rejected (dead-letter topic) <------------/

They are intentionally simple. What matters is that they behave like a real
event-driven estate: they propagate correlation IDs, write structured logs,
commit offsets only after their output is safe, and can be switched into
faulty modes so the harness has real defects to find.
"""
