"""Модели входящих данных. Настоящего клиента CRM в задании нет."""

from typing import Iterator, Type

COMPANIES = "companies"
CONTACTS = "contacts"


class BaseEntity:
    def __init__(self, id: int, name=None, custom_fields_values=None,
                 _embedded=None, status_id=None, **fields):
        self.id = id
        self.name = name
        self.status_id = status_id
        self.custom_fields_values = custom_fields_values or {}
        self._embedded = _embedded or {}
        self.contacts = self._embedded.get(CONTACTS) or []
        self.companies = self._embedded.get(COMPANIES) or []
        self.fields = fields

    def get_field(self, name):
        return (self.custom_fields_values.get(name) or {}).get("value")

    def as_dict(self):
        return {
            **self.fields,
            "id": self.id,
            "name": self.name,
            "status_id": self.status_id,
            "custom_fields_values": self.custom_fields_values,
            "_embedded": self._embedded,
        }


class Customer(BaseEntity):
    pass


class Lead(BaseEntity):
    pass


def amocrm_get(obj_cls: Type[BaseEntity], **params) -> Iterator[BaseEntity]:
    # Нельзя возвращать пустой список: полный проход тогда удалит все данные.
    raise NotImplementedError(
        "Клиент CRM не входит в задание. Подключите реальный amocrm_get "
        "или используйте локальные данные через flask sync-demo."
    )
