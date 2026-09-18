from datetime import date, datetime
from decimal import Decimal

from .extensions import db


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Account(TimestampMixin, db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    account_type = db.Column(db.String(30), nullable=False, default="bank")
    opening_balance = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    institution = db.Column(db.String(80))
    credit_limit = db.Column(db.Numeric(18, 2))
    statement_day = db.Column(db.Integer)
    due_day = db.Column(db.Integer)
    last_four = db.Column(db.String(4))
    note = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    transactions = db.relationship(
        "Transaction",
        foreign_keys="Transaction.account_id",
        back_populates="account",
        lazy="dynamic",
    )


class Category(TimestampMixin, db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="expense")
    icon = db.Column(db.String(16), nullable=False, default="•")
    is_system = db.Column(db.Boolean, nullable=False, default=False)

    __table_args__ = (db.UniqueConstraint("name", "kind", name="uq_category_name_kind"),)


class Transaction(TimestampMixin, db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    occurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    transaction_type = db.Column(db.String(20), nullable=False)  # income, expense, transfer
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    description = db.Column(db.String(255))
    source = db.Column(db.String(20), nullable=False, default="web")

    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True)
    destination_account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), index=True)

    account = db.relationship("Account", foreign_keys=[account_id], back_populates="transactions")
    destination_account = db.relationship("Account", foreign_keys=[destination_account_id])
    category = db.relationship("Category")
    attachments = db.relationship("Attachment", back_populates="transaction", cascade="all, delete-orphan")


class Budget(TimestampMixin, db.Model):
    __tablename__ = "budgets"

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    month = db.Column(db.Integer, nullable=False, index=True)
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True)

    category = db.relationship("Category")
    __table_args__ = (db.UniqueConstraint("year", "month", "category_id", name="uq_budget_month_category"),)


class Bill(TimestampMixin, db.Model):
    __tablename__ = "bills"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    due_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="unpaid")
    recurrence = db.Column(db.String(20), nullable=False, default="none")
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), index=True)
    note = db.Column(db.String(255))
    paid_at = db.Column(db.DateTime)

    category = db.relationship("Category")
    account = db.relationship("Account")


class Attachment(TimestampMixin, db.Model):
    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False, unique=True)
    relative_path = db.Column(db.String(500), nullable=False)
    mime_type = db.Column(db.String(120))
    size_bytes = db.Column(db.Integer, nullable=False, default=0)
    attachment_type = db.Column(db.String(30), nullable=False, default="receipt")
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"), index=True)

    transaction = db.relationship("Transaction", back_populates="attachments")


class AppSetting(TimestampMixin, db.Model):
    __tablename__ = "app_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)


def as_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
