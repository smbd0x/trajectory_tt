from abstract_customer import get_customer_json, get_lead_json
from amocrm import Customer, Lead


def entity(cls, crm_id, login=None, name="Название", contacts=None, companies=None, **fields):
    return cls(
        id=crm_id, name=name,
        custom_fields_values={"Логин": {"value": login}} if login is not None else None,
        _embedded={"contacts": contacts or [], "companies": companies or []},
        **fields,
    )


def saved_row(row_id, customers=(), leads=(), contacts=(), companies=(), send_methods=None):
    return {
        "id": row_id,
        "amo_customers": [get_customer_json(item) for item in customers],
        "amo_leads": [get_lead_json(item) for item in leads],
        "amo_contacts": list(contacts),
        "amo_companies": list(companies),
        "send_methods": send_methods,
    }


def crm_source(customers=(), leads=()):
    def get(cls, **params):
        return iter(customers if cls is Customer else leads)
    return get
