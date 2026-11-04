"""Testing the tester: corrupt stored data on purpose and prove reconciliation notices.

A reconciliation that always says "PASSED" is worse than none. These tests
store a clean run, then make one targeted change per declaration directly in
Oracle - each mimicking a real defect class - and assert the framework flags
exactly those declarations, with the right diagnosis, and nothing else.
"""

import secrets

import pytest

from customs_harness.generator.scenarios import generate
from customs_harness.reconciliation.compare import MISSING_ROW, UNEXPECTED_ROW
from customs_harness.reconciliation.engine import Status


def _pick(oracle, refs, where, exclude):
    candidates = [r for r in refs if r not in exclude]
    binds = {f"r{i}": ref for i, ref in enumerate(candidates)}
    in_list = ", ".join(f":{name}" for name in binds)
    rows = oracle.query(
        f"SELECT declaration_ref FROM declaration d WHERE declaration_ref IN ({in_list}) AND {where}", binds
    )
    assert rows, f"no stored declaration satisfies: {where}"
    ref = rows[0]["DECLARATION_REF"]
    exclude.add(ref)
    return ref


@pytest.fixture(scope="module")
def tampered(run_pipeline, oracle):
    run = run_pipeline(messages=generate(40, {"standard": 3, "multi_item": 1}))
    assert run.wait.complete and not run.wait.rejected
    refs = [e.declaration_ref for e in run.manifest.entries]
    used: set[str] = set()

    rounded = _pick(oracle, refs, "MOD(total_gross_mass_kg * 1000, 10) <> 0", used)
    oracle.execute(
        "UPDATE declaration SET total_gross_mass_kg = ROUND(total_gross_mass_kg, 2)"
        " WHERE declaration_ref = :1",
        [rounded],
    )

    truncated = _pick(oracle, refs, "1 = 1", used)
    oracle.execute(
        "UPDATE goods_item SET description = SUBSTR(description, 1, 10)"
        " WHERE declaration_ref = :1 AND item_number = 1",
        [truncated],
    )

    lost_item = _pick(oracle, refs, "goods_item_count >= 2", used)
    oracle.execute("DELETE FROM goods_item WHERE declaration_ref = :1 AND item_number = 2", [lost_item])

    extra_item = _pick(oracle, refs, "goods_item_count < 99", used)
    oracle.execute(
        "INSERT INTO goods_item (declaration_ref, item_number, commodity_code, description,"
        " country_of_origin, net_mass_kg, item_value)"
        " VALUES (:1, 99, '84713000', 'Phantom item', 'GB', 1, 1)",
        [extra_item],
    )

    flipped_lane = _pick(oracle, refs, "1 = 1", used)
    oracle.execute(
        "UPDATE declaration SET lane = CASE lane WHEN 'RED' THEN 'GREEN' ELSE 'RED' END"
        " WHERE declaration_ref = :1",
        [flipped_lane],
    )

    shouted = _pick(oracle, refs, "consignee_name <> UPPER(consignee_name)", used)
    oracle.execute(
        "UPDATE declaration SET consignee_name = UPPER(consignee_name) WHERE declaration_ref = :1", [shouted]
    )

    deleted = _pick(oracle, refs, "1 = 1", used)
    oracle.execute("DELETE FROM declaration WHERE declaration_ref = :1", [deleted])

    return run, {
        "rounded": rounded,
        "truncated": truncated,
        "lost_item": lost_item,
        "extra_item": extra_item,
        "flipped_lane": flipped_lane,
        "shouted": shouted,
        "deleted": deleted,
    }


@pytest.fixture(scope="module")
def report(tampered, reconcile):
    run, _ = tampered
    return reconcile(run)


@pytest.fixture(scope="module")
def results_by_ref(report):
    return {r.key: r for r in report.results}


def test_only_tampered_declarations_are_flagged(tampered, report):
    run, refs = tampered
    assert set(r.key for r in report.results) == set(refs.values())
    assert report.count(Status.MATCHED) == len(run.messages) - len(refs)
    assert not report.passed


@pytest.mark.parametrize(
    ("case", "field", "signature"),
    [
        ("rounded", "DECLARATION.TOTAL_GROSS_MASS_KG", "rounded to 2 dp"),
        ("truncated", "GOODS_ITEM[1].DESCRIPTION", "truncated to 10 chars"),
        ("flipped_lane", "DECLARATION.LANE", "service disagrees with the 'lane' rule"),
        ("shouted", "DECLARATION.CONSIGNEE_NAME", "letter case differs"),
    ],
)
def test_value_defects_are_diagnosed(case, field, signature, tampered, results_by_ref):
    _, refs = tampered
    result = results_by_ref[refs[case]]
    assert result.status is Status.MISMATCHED
    assert [(d.path, d.hint) for d in result.diffs] == [(field, signature)]


def test_lost_child_row_is_reported(tampered, results_by_ref):
    _, refs = tampered
    (diff,) = results_by_ref[refs["lost_item"]].diffs
    assert (diff.path, diff.kind) == ("GOODS_ITEM[2].*", MISSING_ROW)


def test_unexpected_child_row_is_reported(tampered, results_by_ref):
    _, refs = tampered
    (diff,) = results_by_ref[refs["extra_item"]].diffs
    assert (diff.path, diff.kind) == ("GOODS_ITEM[99].*", UNEXPECTED_ROW)


def test_deleted_declaration_is_missing_not_rejected(tampered, results_by_ref):
    _, refs = tampered
    assert results_by_ref[refs["deleted"]].status is Status.MISSING


def test_two_way_check_finds_rows_that_no_message_explains(run_pipeline, reconcile, oracle):
    run = run_pipeline(count=5, mix={"standard": 1})
    orphan = "26XI" + secrets.token_hex(7).upper()
    oracle.execute(
        """
        INSERT INTO declaration (
            declaration_ref, correlation_id, message_id, message_created_at, source_system,
            declaration_category, movement_type, declarant_eori, consignor_name, consignor_country,
            consignee_name, consignee_town, consignee_country, transport_mode_code, arrival_date,
            ukims_authorised, total_gross_mass_kg, invoice_currency, total_invoice_value,
            goods_item_count, risk_score, lane, assessed_at)
        VALUES (
            :1, 'orphan', 'orphan', SYSTIMESTAMP, 'API',
            'IM', 'GB_TO_NI', 'GB000000000000', 'Ghost Ltd', 'GB',
            'Ghost Ltd', 'Belfast', 'GB', 3, SYSDATE,
            'Y', 1, 'GBP', 1,
            0, 10, 'GREEN', SYSTIMESTAMP)
        """,
        [orphan],
    )
    try:
        report = reconcile(run, two_way=True)
        assert orphan in report.orphans
        assert not report.passed
    finally:
        oracle.execute("DELETE FROM declaration WHERE declaration_ref = :1", [orphan])
