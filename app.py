import json
import logging
import os
from pathlib import Path

import click
from flask import Flask

from amocrm import Customer
from main import collect_leads_customers
from models import db


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", "postgresql+psycopg2://localhost/crm_sync"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    if config:
        app.config.update(config)
    db.init_app(app)

    @app.cli.command("init-db")
    def init_db():
        """Создать таблицу и индексы для новой базы."""
        db.create_all()
        click.echo("Таблица и индексы созданы.")

    @app.cli.command("sync")
    @click.option("--partial", is_flag=True, help="Без очистки отсутствующих сущностей.")
    def sync(partial):
        """Запустить синхронизацию после подключения клиента CRM."""
        count = collect_leads_customers(is_full=not partial)
        click.echo(f"Сохранено записей: {count}")

    @app.cli.command("sync-demo")
    @click.argument("filename", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--partial", is_flag=True, help="Без очистки отсутствующих сущностей.")
    def sync_demo(filename, partial):
        """Прочитать выгрузку из JSON-файла вместо обращения к CRM."""
        data = json.loads(filename.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(
            not isinstance(data.get(key), list) for key in ("customers", "leads")
        ):
            raise click.ClickException("В файле нужны два массива: customers и leads.")

        def crm_get(entity_class, **params):
            key = "customers" if entity_class is Customer else "leads"
            for item in data[key]:
                yield entity_class(**item)

        count = collect_leads_customers(is_full=not partial, crm_get=crm_get)
        click.echo(f"Сохранено записей: {count}")

    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
