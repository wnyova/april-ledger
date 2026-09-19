"""Validation and optimistic concurrency for ledger entry forms."""
import hashlib
import json
from datetime import datetime
from decimal import Decimal, DecimalException

from .extensions import db
from .models import Account, Category
from .services import parse_money


class EditConflict(ValueError):
    pass


def record_revision(record):
    # Use persisted values, not just updated_at (MariaDB may store whole seconds).
    values = {column.name: str(getattr(record, column.name)) for column in record.__table__.columns}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def check_revision(record, submitted):
    if not submitted or submitted != record_revision(record):
        raise EditConflict('This record changed since you opened it. Reload the edit page before saving again.')


def entry_amount(raw):
    try:
        value = parse_money(raw)
    except (DecimalException, ValueError):
        raise ValueError('Enter a valid positive amount.') from None
    if not value.is_finite() or value <= 0 or value >= Decimal('10000000000000000'):
        raise ValueError('Amount must be positive and fit within 16 whole-number digits.')
    return value


def text_value(form, name, limit, required=False):
    value = (form.get(name) or '').strip()
    if required and not value:
        raise ValueError(f'{name.replace("_", " ").title()} is required.')
    if len(value) > limit:
        raise ValueError(f'{name.title()} must be at most {limit} characters.')
    return value or None


def selected_record(model, raw, label, required=False):
    if raw is None or raw == '':
        if required:
            raise ValueError(f'{label} is required.')
        return None
    try:
        record_id = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f'Choose a valid {label.lower()}.') from None
    record = db.session.get(model, record_id)
    if record is None:
        raise ValueError(f'Choose a valid {label.lower()}.')
    return record


def selected_account(raw, original_id=None, required=False):
    account = selected_record(Account, raw, 'Account', required)
    if account and not account.is_active and account.id != original_id:
        raise ValueError('Choose an active account. An existing archived account may only be kept unchanged.')
    return account


def transaction_values(form, original=None):
    kind = (form.get('transaction_type') or '').lower()
    if kind not in {'income', 'expense', 'transfer'}:
        raise ValueError('Invalid transaction type.')
    amount = entry_amount(form.get('amount'))
    account = selected_account(form.get('account_id'), original.account_id if original else None, required=True)
    destination = None
    category = None
    if kind == 'transfer':
        destination = selected_account(form.get('destination_account_id'),
                                       original.destination_account_id if original else None, required=True)
        if destination.id == account.id:
            raise ValueError('Transfer destination must be a different account.')
    else:
        keep_uncategorized = original is not None and original.category_id is None and original.transaction_type == kind
        category = selected_record(Category, form.get('category_id'), 'Category', required=not keep_uncategorized)
        if category and category.kind != kind:
            raise ValueError(f'Choose a valid {kind} category.')
    raw_date = form.get('occurred_at') or ''
    occurred_at = None
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            occurred_at = datetime.strptime(raw_date, fmt)
            break
        except ValueError:
            pass
    if occurred_at is None:
        raise ValueError('Enter a valid date and time.')
    return dict(transaction_type=kind, amount=amount, account=account,
                destination_account=destination, category=category, occurred_at=occurred_at,
                description=text_value(form, 'description', 255))


def bill_values(form, original=None):
    name = text_value(form, 'name', 120, required=True)
    amount = entry_amount(form.get('amount'))
    try:
        due_date = datetime.strptime(form.get('due_date') or '', '%Y-%m-%d').date()
    except ValueError:
        raise ValueError('Enter a valid due date.') from None
    recurrence = form.get('recurrence', 'none')
    if recurrence not in {'none', 'monthly', 'yearly'}:
        raise ValueError('Choose one-time, monthly, or yearly recurrence.')
    account = selected_account(form.get('account_id'), original.account_id if original else None)
    category = selected_record(Category, form.get('category_id'), 'Category')
    if category and category.kind != 'expense':
        raise ValueError('Choose an expense category.')
    if original:
        if 'status' in form and form['status'] != original.status:
            raise ValueError('Use the bill payment action to change payment status.')
        if original.status == 'paid' and (
            amount != original.amount or recurrence != original.recurrence
            or (account.id if account else None) != original.account_id
            or (category.id if category else None) != original.category_id
        ):
            raise ValueError('Paid bill amounts, accounts, categories, and recurrence are locked. '
                             'Correct the recorded payment in Transactions; edit the next unpaid bill for future payments.')
    return dict(name=name, amount=amount, due_date=due_date, recurrence=recurrence,
                account=account, category=category, note=text_value(form, 'note', 255))
