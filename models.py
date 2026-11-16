from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Column, Index, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB

db = SQLAlchemy(metadata=MetaData(naming_convention={
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}))


class AbstractCustomer(db.Model):
    __tablename__ = "abstract_customer"

    id = Column(db.BigInteger(), primary_key=True)
    amo_customers = Column(JSONB)
    amo_leads = Column(JSONB)
    amo_companies = Column(JSONB)
    amo_contacts = Column(JSONB)
    send_methods = Column(JSONB)
    created_at = Column(db.DateTime, server_default=func.now())
    updated_at = Column(db.DateTime, onupdate=func.now())

    # @> ищет точный id, логин или имя внутри general в JSON-массиве.
    __table_args__ = (
        Index("ix_abstract_customer_amo_customers_gin", amo_customers,
              postgresql_using="gin",
              postgresql_ops={"amo_customers": "jsonb_path_ops"}),
        Index("ix_abstract_customer_amo_leads_gin", amo_leads,
              postgresql_using="gin",
              postgresql_ops={"amo_leads": "jsonb_path_ops"}),
    )
