import pytest

from abstract_customer import get_related_json
from amocrm import Customer, Lead, amocrm_get
from main import SyncState
from tests.helpers import entity, saved_row


def test_customer_without_login_is_skipped():
    state = SyncState([])
    state.apply_customer(entity(Customer, 1))
    assert not state.rows


def test_customer_id_has_priority_over_login():
    state = SyncState([
        saved_row(1, customers=[entity(Customer, 10, "old")]),
        saved_row(2, leads=[entity(Lead, 20, "new")]),
    ])
    state.apply_customer(entity(Customer, 10, "new", name="Обновлено"))
    assert state.rows[1]["amo_customers"][10]["general"]["name"] == "Обновлено"
    assert not state.rows[2]["amo_customers"]


def test_customer_matches_saved_lead_by_login():
    state = SyncState([saved_row(1, leads=[entity(Lead, 20, "same")])])
    state.apply_customer(entity(Customer, 10, "same"))
    assert state.by_id["amo_customers"][10] == 1
    assert len(state.rows) == 1


@pytest.mark.parametrize("primary", ["amo_customers", "amo_leads"])
def test_ambiguous_login_is_not_used(primary):
    if primary == "amo_customers":
        state = SyncState([
            saved_row(1, customers=[entity(Customer, 10, "same")]),
            saved_row(2, customers=[entity(Customer, 11, "same")]),
        ])
        state.apply_lead(entity(Lead, 20, "same"))
        assert state.by_id["amo_leads"][20] < 0
    else:
        state = SyncState([
            saved_row(1, leads=[entity(Lead, 20, "same")]),
            saved_row(2, leads=[entity(Lead, 21, "same")]),
        ])
        state.apply_customer(entity(Customer, 10, "same"))
        assert state.by_id["amo_customers"][10] < 0
    assert len(state.rows) == 3


def test_two_entities_in_one_row_do_not_make_login_ambiguous():
    state = SyncState([saved_row(1, leads=[
        entity(Lead, 20, "same"), entity(Lead, 21, "same"),
    ])])
    state.apply_customer(entity(Customer, 10, "same"))
    assert state.by_id["amo_customers"][10] == 1


def test_merge_keeps_lead_row_and_fresh_data_and_updates_indexes():
    state = SyncState([
        saved_row(1, customers=[entity(Customer, 10, "same")],
                  leads=[entity(Lead, 21, "other")],
                  send_methods=[{"type": "mail", "value": "a@example.com"}]),
        saved_row(2, leads=[entity(Lead, 20, "old")], contacts=[
            get_related_json({"id": 30, "name": "удалить старый контакт"}),
        ]),
    ])
    state.apply_customer(entity(Customer, 10, "same", contacts=[{"id": 30, "name": "Контакт"}]))
    state.apply_lead(entity(Lead, 20, "same", name="Свежая сделка"))
    # Следующая сделка находит покупателя уже в целевой записи.
    state.apply_lead(entity(Lead, 22, "same"))
    # И сделка, перенесённая из удалённой записи, по-прежнему находится по id.
    state.apply_lead(entity(Lead, 21, "other", name="Тоже обновлена"))
    state.cleanup()
    assert set(state.rows) == {2}
    assert state.deleted_ids == {1}
    row = state.rows[2]
    assert set(row["amo_customers"]) == {10}
    assert set(row["amo_leads"]) == {20, 21, 22}
    assert row["amo_leads"][20]["general"]["name"] == "Свежая сделка"
    assert row["amo_leads"][21]["general"]["name"] == "Тоже обновлена"
    assert set(row["amo_contacts"]) == {30}
    assert row["amo_contacts"][30]["general"]["name"] == "Контакт"
    assert row["send_methods"] == [{"type": "mail", "value": "a@example.com"}]


def test_changed_login_does_not_leave_false_match():
    state = SyncState([saved_row(1, customers=[entity(Customer, 10, "old")])])
    state.apply_customer(entity(Customer, 10, "new"))
    state.apply_lead(entity(Lead, 20, "old"))
    state.apply_lead(entity(Lead, 21, "new"))
    assert state.by_id["amo_leads"][20] != 1
    assert state.by_id["amo_leads"][21] == 1


def test_changing_one_of_several_logins_keeps_other_entity_searchable():
    state = SyncState([saved_row(1, customers=[
        entity(Customer, 10, "old"), entity(Customer, 11, "old"),
    ])])
    state.apply_customer(entity(Customer, 10, "new"))
    state.apply_lead(entity(Lead, 20, "old"))
    assert state.by_id["amo_leads"][20] == 1


def test_lead_without_login_is_created_then_updated_by_id():
    state = SyncState([])
    state.apply_lead(entity(Lead, 20, name="Первая версия"))
    state.apply_lead(entity(Lead, 20, name="Вторая версия"))
    assert len(state.rows) == 1
    row_id = state.by_id["amo_leads"][20]
    assert state.rows[row_id]["amo_leads"][20]["general"]["name"] == "Вторая версия"


def test_cleanup_removes_missing_and_marked_entities_but_handles_null_name():
    state = SyncState([
        saved_row(1, customers=[entity(Customer, 10, "same")],
                  leads=[entity(Lead, 20, "same"), entity(Lead, 21)]),
        saved_row(2, leads=[entity(Lead, 99)]),
    ])
    state.apply_customer(entity(Customer, 10, "same", name=None, contacts=[
        {"id": 30, "name": "Живой"}, {"id": 31, "name": "УДАЛИТЬ контакт"},
    ], companies=[{"id": 40, "name": "Удалить компанию"}, {"id": 41, "name": None}]))
    state.apply_lead(entity(Lead, 20, "same", name="Нужно УдАлИтЬ"))
    state.cleanup()
    assert set(state.rows) == {1}
    row = state.rows[1]
    assert set(row["amo_customers"]) == {10}
    assert not row["amo_leads"]
    assert set(row["amo_contacts"]) == {30}
    assert set(row["amo_companies"]) == {41}
    assert state.deleted_ids == {2}


def test_children_of_deleted_parent_are_removed():
    state = SyncState([])
    state.apply_customer(entity(Customer, 10, "same", name="удалить", contacts=[{"id": 30}]))
    state.apply_lead(entity(Lead, 20, "same", contacts=[{"id": 31}]))
    state.cleanup()
    row = next(iter(state.rows.values()))
    assert not row["amo_customers"]
    assert set(row["amo_leads"]) == {20}
    assert set(row["amo_contacts"]) == {31}


def test_related_id_only_does_not_erase_existing_details():
    contact = get_related_json({"id": 30, "name": "Иван", "phone": "+70000000000"})
    state = SyncState([saved_row(1, customers=[entity(Customer, 10, "same")], contacts=[contact])])
    state.apply_customer(entity(Customer, 10, "same", contacts=[{"id": 30}]))
    state.cleanup()
    assert state.rows[1]["amo_contacts"][30] == contact


@pytest.mark.parametrize("related", ["contacts", "companies"])
@pytest.mark.parametrize("details_in_source", [False, True])
def test_merge_keeps_related_details_with_id_only_links(related, details_in_source):
    details = {"id": 30, "name": "Сохранённые подробности"}
    source_value = details if details_in_source else {"id": 30}
    target_value = {"id": 30} if details_in_source else details
    state = SyncState([
        saved_row(1, customers=[entity(Customer, 10, "same")],
                  **{related: [get_related_json(source_value)]}),
        saved_row(2, leads=[entity(Lead, 20, "old")],
                  **{related: [get_related_json(target_value)]}),
    ])
    links = {related: [{"id": 30}]}
    state.apply_customer(entity(Customer, 10, "same", **links))
    state.apply_lead(entity(Lead, 20, "same", **links))
    state.cleanup()
    assert set(state.rows) == {2}
    stored = state.rows[2]["amo_" + related][30]
    assert stored["general"]["name"] == "Сохранённые подробности"
    assert stored["api_value"]["name"] == "Сохранённые подробности"


@pytest.mark.parametrize("related", ["contacts", "companies"])
def test_shared_related_update_reaches_rows_processed_earlier(related):
    state = SyncState([
        saved_row(1, customers=[entity(Customer, 10, "first")], **{related: [
            get_related_json({"id": 30, "name": "удалить старое имя"}),
        ]}),
        saved_row(2, customers=[entity(Customer, 11, "second")]),
    ])
    state.apply_customer(entity(Customer, 10, "first", **{related: [{"id": 30}]}))
    state.apply_customer(entity(Customer, 11, "second", **{related: [
        {"id": 30, "name": "Актуальное имя"},
    ]}))
    state.cleanup()
    assert set(state.rows) == {1, 2}
    for row in state.rows.values():
        assert row["amo_" + related][30]["general"]["name"] == "Актуальное имя"


def test_empty_full_export_removes_every_row():
    state = SyncState([saved_row(1, leads=[entity(Lead, 20)])])
    state.cleanup()
    assert not state.rows
    assert state.deleted_ids == {1}


def test_duplicate_crm_id_in_different_rows_is_rejected():
    with pytest.raises(ValueError, match="20"):
        SyncState([
            saved_row(1, leads=[entity(Lead, 20)]),
            saved_row(2, leads=[entity(Lead, 20)]),
        ])


def test_missing_crm_client_cannot_look_like_empty_successful_export():
    with pytest.raises(NotImplementedError):
        list(amocrm_get(Customer))
