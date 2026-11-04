"""At-least-once delivery means redelivery: the pipeline must store each declaration once."""

from customs_harness.generator.scenarios import VALID_MIX
from customs_harness.reconciliation.engine import Status


def test_redelivered_messages_are_stored_exactly_once(run_pipeline, reconcile, oracle):
    run = run_pipeline(count=40, mix=VALID_MIX, duplicate_every=2)
    refs = [e.declaration_ref for e in run.manifest.entries]
    assert sum(e.copies for e in run.manifest.entries) == 60

    rows = oracle.fetch_in("DECLARATION", "DECLARATION_REF", refs)
    assert len(rows) == 40
    expected_items = sum(len(m.declaration.goods_items) for m in run.messages)
    assert len(oracle.fetch_in("GOODS_ITEM", "DECLARATION_REF", refs)) == expected_items

    report = reconcile(run)
    assert report.duplicate_messages == 20
    assert report.count(Status.MATCHED) == 60  # both copies match the single stored row
    assert report.passed
