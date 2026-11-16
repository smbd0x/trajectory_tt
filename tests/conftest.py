import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app import create_app
from models import db


@pytest.fixture
def pg_app():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Задайте TEST_DATABASE_URL для интеграционных тестов PostgreSQL")
    # Отдельная случайная схема: таблицы пользователя тесты не затрагивают.
    schema = "test_sync_" + uuid4().hex
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": url,
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "connect_args": {"options": f"-csearch_path={schema}"},
        },
    })
    try:
        with app.app_context():
            db.create_all()
            try:
                yield app
            finally:
                db.session.remove()
                db.engine.dispose()
    finally:
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()
