import logging
from collections import defaultdict
from itertools import islice

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from abstract_customer import get_customer_json, get_lead_json, get_related_json
from amocrm import COMPANIES, CONTACTS, Customer, Lead, amocrm_get
from models import AbstractCustomer, db

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
PRIMARY_FIELDS = ("amo_customers", "amo_leads")
RELATED_FIELDS = ("amo_contacts", "amo_companies")
JSON_FIELDS = PRIMARY_FIELDS + RELATED_FIELDS


def batches(values, size=BATCH_SIZE):
    iterator = iter(values)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch


def marked_for_deletion(entity):
    return "удалить" in (entity["general"].get("name") or "").lower()


class SyncState:
    """Данные одного прохода. Все поиски и слияния здесь идут без SQL."""

    def __init__(self, rows):
        self.rows = {}
        self.by_id = {field: {} for field in PRIMARY_FIELDS}
        self.by_login = {field: defaultdict(set) for field in PRIMARY_FIELDS}
        self.related_by_id = {field: {} for field in RELATED_FIELDS}
        self.seen = {field: set() for field in PRIMARY_FIELDS}
        self.deleted_ids = set()
        self.next_id = -1

        for saved_row in rows:
            row = dict(saved_row)
            # В памяти словари по CRM id; в Postgres остаются JSON-массивы.
            for field in JSON_FIELDS:
                row[field] = {item["general"]["id"]: item
                              for item in row[field] or []}
            # Общий контакт или компания имеют одну версию во всех строках.
            for field in RELATED_FIELDS:
                for crm_id, item in row[field].items():
                    shared = self.related_by_id[field].setdefault(
                        crm_id, {"general": {}, "api_value": {}},
                    )
                    for part in ("general", "api_value"):
                        shared[part].update(item.get(part) or {})
                    row[field][crm_id] = shared
            self.rows[row["id"]] = row
            for field in PRIMARY_FIELDS:
                for crm_id, item in row[field].items():
                    if crm_id in self.by_id[field]:
                        raise ValueError(f"Повторный CRM id: {field} / {crm_id}")
                    self.by_id[field][crm_id] = row["id"]
                    login = item["general"].get("login")
                    if login:
                        self.by_login[field][login].add(crm_id)

    def find_by_login(self, field, login):
        if not login:
            return None
        crm_ids = self.by_login[field].get(login, ())
        row_ids = {self.by_id[field][crm_id] for crm_id in crm_ids}
        # Считаем записи AbstractCustomer, а не количество вложенных сущностей.
        if len(row_ids) == 1:
            return next(iter(row_ids))
        if len(row_ids) > 1:
            logger.warning("Неоднозначный логин %r в %s", login, field)
        return None

    def new_row(self):
        # Временный ключ нужен только в памяти, настоящий id выдаст Postgres.
        row_id = self.next_id
        self.next_id -= 1
        self.rows[row_id] = {
            "id": row_id, "send_methods": None,
            **{field: {} for field in JSON_FIELDS},
        }
        return row_id

    def put_entity(self, row_id, field, entity):
        crm_id = entity["general"]["id"]
        previous = self.rows[row_id][field].get(crm_id)
        old_login = previous["general"].get("login") if previous else None
        if old_login:
            old_ids = self.by_login[field][old_login]
            old_ids.discard(crm_id)
            if not old_ids:
                del self.by_login[field][old_login]
        self.rows[row_id][field][crm_id] = entity
        self.by_id[field][crm_id] = row_id
        login = entity["general"].get("login")
        if login:
            self.by_login[field][login].add(crm_id)
        self.seen[field].add(crm_id)

    def put_related(self, row_id, entity):
        for field, values in (("amo_contacts", entity.contacts),
                              ("amo_companies", entity.companies)):
            stored = self.rows[row_id][field]
            for value in values:
                shared = self.related_by_id[field].setdefault(value["id"], {})
                # Обновляем тот же объект: слияние не вернёт старую копию,
                # а ссылка только с id не затрёт уже известные подробности.
                shared.update(get_related_json(value, shared))
                stored[value["id"]] = shared

    def merge(self, target_id, source_id):
        target, source = self.rows[target_id], self.rows[source_id]
        for field in JSON_FIELDS:
            target[field].update(source[field])
        for field in PRIMARY_FIELDS:
            for crm_id in source[field]:
                self.by_id[field][crm_id] = target_id
        # Настройки уведомлений не относятся к выгрузке CRM, их не теряем.
        if source["send_methods"]:
            methods = list(target["send_methods"] or [])
            for method in source["send_methods"]:
                if method not in methods:
                    methods.append(method)
            target["send_methods"] = methods
        self.remove_row(source_id)

    def remove_row(self, row_id):
        del self.rows[row_id]
        if row_id > 0:
            self.deleted_ids.add(row_id)

    def apply_customer(self, customer):
        login = customer.get_field("Логин")
        if not login:
            return
        row_id = self.by_id["amo_customers"].get(customer.id)
        if row_id is None:
            row_id = self.find_by_login("amo_leads", login)
        if row_id is None:
            row_id = self.new_row()
        self.put_entity(row_id, "amo_customers", get_customer_json(customer))
        self.put_related(row_id, customer)

    def apply_lead(self, lead):
        row_id = self.by_id["amo_leads"].get(lead.id)
        login_row_id = self.find_by_login("amo_customers", lead.get_field("Логин"))
        if row_id is not None and login_row_id is not None and row_id != login_row_id:
            self.merge(row_id, login_row_id)
        elif row_id is None:
            row_id = login_row_id
        if row_id is None:
            row_id = self.new_row()
        # Даже после слияния обновляем текущую сделку свежими данными CRM.
        self.put_entity(row_id, "amo_leads", get_lead_json(lead))
        self.put_related(row_id, lead)

    def cleanup(self):
        for row_id, row in list(self.rows.items()):
            for field in PRIMARY_FIELDS:
                row[field] = {
                    crm_id: item for crm_id, item in row[field].items()
                    if crm_id in self.seen[field] and not marked_for_deletion(item)
                }
            # Вложенные сущности оставляем только у сохранившихся родителей.
            linked = {field: set() for field in RELATED_FIELDS}
            for field in PRIMARY_FIELDS:
                for item in row[field].values():
                    embedded = item["api_value"].get("_embedded") or {}
                    for related in RELATED_FIELDS:
                        key = related.removeprefix("amo_")
                        linked[related].update(value["id"] for value in embedded.get(key) or [])
            for field in RELATED_FIELDS:
                row[field] = {
                    crm_id: item for crm_id, item in row[field].items()
                    if crm_id in linked[field] and not marked_for_deletion(item)
                }
            if not row["amo_customers"] and not row["amo_leads"]:
                self.remove_row(row_id)


def save_state(connection, state):
    table = AbstractCustomer.__table__
    fields = JSON_FIELDS + ("send_methods",)
    # Новым записям id не передаём. Существующие пишем пакетным upsert.
    for is_new in (False, True):
        rows = (row for row_id, row in state.rows.items() if (row_id < 0) == is_new)
        for batch in batches(rows):
            values = []
            for row in batch:
                value = {field: list(row[field].values()) for field in JSON_FIELDS}
                value["send_methods"] = row["send_methods"]
                if not is_new:
                    value["id"] = row["id"]
                values.append(value)
            statement = insert(table).values(values)
            if not is_new:
                statement = statement.on_conflict_do_update(
                    index_elements=[table.c.id],
                    set_={**{field: statement.excluded[field] for field in fields},
                          "updated_at": func.now()},
                )
            connection.execute(statement)
    for batch in batches(state.deleted_ids):
        connection.execute(delete(table).where(table.c.id.in_(batch)))


def collect_leads_customers(is_full=True, crm_get=None):
    """Один полный или частичный проход. Требует контекст Flask-приложения."""
    table = AbstractCustomer.__table__
    crm_get = crm_get or amocrm_get
    try:
        with db.engine.begin() as connection:
            # Два ночных запуска не должны перезаписывать результаты друг друга.
            locked = connection.scalar(text("SELECT pg_try_advisory_xact_lock(2026, 1)"))
            if not locked:
                raise RuntimeError("Синхронизация уже выполняется")
            columns = [table.c.id, *(table.c[field] for field in JSON_FIELDS),
                       table.c.send_methods]
            state = SyncState(connection.execute(select(*columns)).mappings())
            for customer in crm_get(Customer, _with=f"{COMPANIES},{CONTACTS}"):
                if not is_full and customer.status_id == 111349:
                    continue
                state.apply_customer(customer)
            for lead in crm_get(Lead, _with=f"{COMPANIES},{CONTACTS}"):
                if not is_full and lead.status_id == 143:
                    continue
                state.apply_lead(lead)
            if is_full:
                state.cleanup()
            save_state(connection, state)
            saved_count = len(state.rows)
        logger.info("Синхронизация завершена: записей %s, полный проход %s",
                    saved_count, is_full)
    except Exception:
        logger.exception("Синхронизация прервана, транзакция отменена")
        raise
    return saved_count
