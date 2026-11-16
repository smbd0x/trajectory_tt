"""Подготовка JSON без запросов и commit внутри обработки сущности."""

from amocrm import Customer, Lead


def get_customer_json(customer: Customer) -> dict:
    return {
        "general": {
            "id": customer.id,
            "login": customer.get_field("Логин"),
            "billing_type": customer.get_field("Вид оплаты"),
            "is_robot": bool(customer.get_field("Робот")),
            "is_nds": bool(customer.get_field("Оплата с НДС")),
            "system": customer.get_field("Система"),
            "host": customer.get_field("Хост"),
            "name": customer.name,
        },
        "api_value": customer.as_dict(),
    }


def get_lead_json(lead: Lead) -> dict:
    return {
        "general": {
            "id": lead.id,
            "login": lead.get_field("Логин"),
            "name": lead.name,
        },
        "api_value": lead.as_dict(),
    }


def get_related_json(value: dict, previous=None) -> dict:
    """CRM может вернуть только id связи; старые подробности сохраняем."""
    previous = previous or {}
    general = dict(previous.get("general") or {})
    for field in ("id", "name", "phone", "inn"):
        if field in value:
            general[field] = value[field]
    return {
        "general": general,
        "api_value": {**(previous.get("api_value") or {}), **value},
    }
