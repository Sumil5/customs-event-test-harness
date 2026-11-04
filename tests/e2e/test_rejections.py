"""Invalid traffic: dead-lettered with the right reason, never stored."""

import pytest

from customs_harness.generator.scenarios import ACCEPTED, SCENARIOS, generate
from customs_harness.kafka import read_topic

INVALID = [name for name, s in SCENARIOS.items() if s.expected_outcome != ACCEPTED]


@pytest.fixture(scope="module")
def rejection_run(run_pipeline):
    return run_pipeline(messages=[m for name in INVALID for m in generate(3, {name: 1})])


@pytest.fixture(scope="module")
def dead_letters(rejection_run, settings):
    return {
        r.correlation_id: r
        for r in read_topic(
            settings.kafka_bootstrap_servers,
            settings.topic_rejected,
            since=rejection_run.manifest.read_from,
            run_id=rejection_run.run_id,
        )
    }


@pytest.mark.parametrize("scenario", INVALID)
def test_invalid_message_is_dead_lettered_with_its_reason_code(scenario, rejection_run, dead_letters):
    entries = [e for e in rejection_run.manifest.entries if e.scenario == scenario]
    assert entries
    for entry in entries:
        record = dead_letters.get(entry.correlation_id)
        assert record is not None, f"{scenario} message was not dead-lettered"
        assert record.headers["x-rejection-code"] == entry.expected_outcome
        assert record.headers["x-rejection-reason"]


def test_rejected_messages_never_reach_oracle(rejection_run):
    assert rejection_run.wait.complete
    assert not rejection_run.wait.persisted


def test_dead_letter_keeps_the_original_payload_and_provenance(rejection_run, dead_letters, settings):
    originals = {m.correlation_id: m.payload for m in rejection_run.messages}
    for cid, record in dead_letters.items():
        assert record.value == originals[cid], "payload altered on the way to the dead-letter topic"
        assert record.headers["x-rejected-by"] == "intake"
        assert record.headers["x-source-topic"] == settings.topic_inbound
