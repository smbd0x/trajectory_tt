import json
from pathlib import Path

import pytest
from sqlalchemy import event, insert, select, text
from sqlalchemy.exc import StatementError

from amocrm import Customer, Lead
from main import BATCH_SIZE, collect_leads_customers
from models import AbstractCustomer, db
from tests.helpers import crm_source, entity, saved_row

pytestmark = pytest.mark.postgres
TABLE = AbstractCustomer.__table__


def seed(**entities):
    row = saved_row(0, **entities)
    del row["id"]
    with db.engine.begin() as connection:
        return connection.scalar(insert(TABLE).values(row).returning(TABLE.c.id))


def read_rows():
    with db.engine.connect() as connection:
        return [dict(row) for row in connection.execute(
            select(TABLE).order_by(TABLE.c.id)
        ).mappings()]


def test_demo_runs_twice_without_duplicates(pg_app):
    runner = pg_app.test_cli_runner()
    example = Path(__file__).resolve().parents[1] / "examples" / "crm.json"
    assert runner.invoke(args=["init-db"]).exit_code == 0
    result = runner.invoke(args=["sync-demo", str(example)])
    assert result.exit_code == 0, result.output
    first = read_rows()
    result = runner.invoke(args=["sync-demo", str(example)])
    assert result.exit_code == 0, result.output
    second = read_rows()
    assert len(first) == len(second) == 2
    assert [row["id"] for row in first] == [row["id"] for row in second]
    assert [row["created_at"] for row in first] == [row["created_at"] for row in second]
    assert all(row["updated_at"] is not None for row in second)
    customer_row = next(row for row in second if row["amo_customers"])
    assert customer_row["amo_leads"][0]["general"]["id"] == 201
    assert customer_row["amo_contacts"][0]["general"]["name"] == "Иван"


def test_merge_is_saved_in_lead_row_and_current_lead_is_updated(pg_app):
    source_id = seed(customers=[entity(Customer, 10, "same")])
    target_id = seed(leads=[entity(Lead, 20, "old")])
    collect_leads_customers(crm_get=crm_source(
        customers=[entity(Customer, 10, "same", contacts=[{"id": 30}])],
        leads=[entity(Lead, 20, "same", name="Свежая версия")],
    ))
    rows = read_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == target_id != source_id
    assert rows[0]["amo_leads"][0]["general"]["name"] == "Свежая версия"
    assert rows[0]["amo_customers"][0]["general"]["id"] == 10
    assert rows[0]["amo_contacts"][0]["general"]["id"] == 30


@pytest.mark.parametrize("failed_class", [Customer, Lead])
def test_interrupted_crm_iterator_leaves_database_unchanged(pg_app, failed_class):
    seed(customers=[entity(Customer, 10, "same")], leads=[entity(Lead, 20, "same")])
    seed(leads=[entity(Lead, 99)])
    before = read_rows()

    def broken_crm(cls, **params):
        yield entity(cls, 10 if cls is Customer else 20, "same", name="Изменено")
        if cls is failed_class:
            raise ConnectionError("Выгрузка оборвалась на следующей странице")

    with pytest.raises(ConnectionError):
        collect_leads_customers(crm_get=broken_crm)
    assert read_rows() == before


def test_error_in_later_write_batch_rolls_back_previous_writes(pg_app):
    seed(leads=[entity(Lead, 20, name="Старая версия")])
    before = read_rows()
    leads = [entity(Lead, 20, name="Изменено")]
    leads.extend(entity(Lead, 1000 + number) for number in range(BATCH_SIZE))
    # Объект нельзя сериализовать в JSON. Ошибка возникнет во втором INSERT новых строк.
    leads.append(entity(Lead, 99999, unsupported_value=object()))
    with pytest.raises(StatementError):
        collect_leads_customers(crm_get=crm_source(leads=leads))
    assert read_rows() == before


def test_query_count_grows_by_batches_and_commit_is_once(pg_app):
    statements, commits = [], []

    def on_statement(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def on_commit(connection):
        commits.append(True)

    event.listen(db.engine, "before_cursor_execute", on_statement)
    event.listen(db.engine, "commit", on_commit)
    try:
        source = crm_source(leads=[entity(Lead, number) for number in range(1, 1002)])
        # Первый проход — INSERT, второй — UPSERT существующих строк.
        for _ in range(2):
            statements.clear()
            commits.clear()
            assert collect_leads_customers(crm_get=source) == 1001
            assert len(statements) == 5  # lock + SELECT таблицы + 3 пакета
            assert sum(sql.startswith("SELECT") for sql in statements) == 2
            assert sum(sql.startswith("INSERT") for sql in statements) == 3
            assert len(commits) == 1
    finally:
        event.remove(db.engine, "before_cursor_execute", on_statement)
        event.remove(db.engine, "commit", on_commit)


def test_partial_sync_keeps_absent_and_marked_entities_and_skips_statuses(pg_app):
    seed(customers=[entity(Customer, 10, "keep")], leads=[entity(Lead, 20, "keep")])
    collect_leads_customers(is_full=False, crm_get=crm_source(
        customers=[entity(Customer, 11, "skip", status_id=111349),
                   entity(Customer, 12, "keep", name="удалить")],
        leads=[entity(Lead, 21, status_id=143), entity(Lead, 22, name="удалить")],
    ))
    rows = read_rows()
    assert {item["general"]["id"] for row in rows for item in row["amo_customers"]} == {10, 12}
    assert {item["general"]["id"] for row in rows for item in row["amo_leads"]} == {20, 22}


def test_full_sync_includes_statuses_skipped_in_partial_mode(pg_app):
    collect_leads_customers(crm_get=crm_source(
        customers=[entity(Customer, 10, "same", status_id=111349)],
        leads=[entity(Lead, 20, "same", status_id=143)],
    ))
    rows = read_rows()
    assert len(rows) == 1
    assert rows[0]["amo_customers"][0]["general"]["id"] == 10
    assert rows[0]["amo_leads"][0]["general"]["id"] == 20


def test_empty_successful_full_export_deletes_old_rows(pg_app):
    seed(leads=[entity(Lead, 20)])
    assert collect_leads_customers(crm_get=crm_source()) == 0
    assert not read_rows()


def test_concurrent_sync_is_rejected(pg_app):
    seed(leads=[entity(Lead, 20)])
    before = read_rows()
    with db.engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(2026, 1)"))
        with pytest.raises(RuntimeError, match="уже выполняется"):
            collect_leads_customers(crm_get=crm_source())
    assert read_rows() == before


def test_exact_id_login_and_name_queries_can_use_gin_indexes(pg_app):
    row_id = seed(customers=[entity(Customer, 10, "exact", name="Компания")],
                  leads=[entity(Lead, 20, "exact", name="Сделка")])
    with db.engine.begin() as connection:
        connection.execute(text("SET LOCAL enable_seqscan = off"))
        for field, crm_id, name in (("amo_customers", 10, "Компания"),
                                    ("amo_leads", 20, "Сделка")):
            for key, value in (("id", crm_id), ("login", "exact"), ("name", name)):
                query = select(TABLE.c.id).where(TABLE.c[field].contains([
                    {"general": {key: value}},
                ]))
                assert connection.scalars(query).all() == [row_id]
                plan = connection.scalars(text(
                    f"EXPLAIN (COSTS OFF) SELECT id FROM abstract_customer "
                    f"WHERE {field} @> CAST(:value AS jsonb)"
                ), {"value": json.dumps([{"general": {key: value}}])}).all()
                assert f"ix_abstract_customer_{field}_gin" in "\n".join(plan)
            assert connection.scalars(select(TABLE.c.id).where(
                TABLE.c[field].contains([{"general": {"login": "exa"}}])
            )).all() == []


def test_invalid_demo_file_does_not_start_cleanup(pg_app, tmp_path):
    seed(leads=[entity(Lead, 20)])
    before = read_rows()
    path = tmp_path / "bad.json"
    path.write_text('{"customers": []}', encoding="utf-8")
    result = pg_app.test_cli_runner().invoke(args=["sync-demo", str(path)])
    assert result.exit_code != 0
    assert read_rows() == before
