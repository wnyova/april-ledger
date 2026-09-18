from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from sqlalchemy import case, func, or_
from werkzeug.utils import secure_filename

from .extensions import db
from .models import Account, Attachment, Bill, Budget, Category, Transaction, as_decimal


ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf", "csv", "txt"}


def parse_money(raw):
    if raw is None:
        raise ValueError("Amount is required")
    cleaned = str(raw).strip().replace("Rp", "").replace(" ", "")
    # Friendly input: 45.000 / 45,000 / 45000.50
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            cleaned = "".join(parts)
        else:
            cleaned = ".".join(parts)
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            cleaned = "".join(parts)
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid amount")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero")
    return amount.quantize(Decimal("0.01"))


def month_bounds(year=None, month=None):
    today = date.today()
    year = int(year or today.year)
    month = int(month or today.month)
    start = datetime.combine(date(year, month, 1), time.min)
    if month == 12:
        next_month = datetime.combine(date(year + 1, 1, 1), time.min)
    else:
        next_month = datetime.combine(date(year, month + 1, 1), time.min)
    return start, next_month


def account_balances(include_inactive=False):
    """Return asset balances and positive outstanding credit-card debt.

    A card purchase increases the card's amount due. A transfer into a card is
    treated as a payment, so it reduces card debt without creating a second
    expense.
    """
    query = Account.query
    if not include_inactive:
        query = query.filter_by(is_active=True)
    accounts = query.order_by(Account.name).all()
    by_id = {account.id: account for account in accounts}
    balances = {account.id: as_decimal(account.opening_balance) for account in accounts}
    if not by_id:
        return accounts, balances

    rows = Transaction.query.filter(
        or_(
            Transaction.account_id.in_(list(by_id)),
            Transaction.destination_account_id.in_(list(by_id)),
        )
    ).all()
    for tx in rows:
        amount = as_decimal(tx.amount)
        source = by_id.get(tx.account_id)
        if source:
            if source.account_type == "credit":
                balances[source.id] += amount if tx.transaction_type in {"expense", "transfer"} else -amount
            else:
                balances[source.id] += amount if tx.transaction_type == "income" else -amount

        destination = by_id.get(tx.destination_account_id)
        if destination and tx.transaction_type == "transfer":
            balances[destination.id] += -amount if destination.account_type == "credit" else amount
    return accounts, balances


def account_totals(accounts, balances):
    assets = sum(
        (balances.get(a.id, Decimal("0")) for a in accounts if a.account_type != "credit"),
        Decimal("0"),
    )
    credit_debt = sum(
        (balances.get(a.id, Decimal("0")) for a in accounts if a.account_type == "credit"),
        Decimal("0"),
    )
    return {"assets": assets, "credit_debt": credit_debt, "net_worth": assets - credit_debt}


def monthly_summary(year=None, month=None):
    start, end = month_bounds(year, month)
    rows = (
        db.session.query(Transaction.transaction_type, func.coalesce(func.sum(Transaction.amount), 0))
        .filter(Transaction.occurred_at >= start, Transaction.occurred_at < end)
        .group_by(Transaction.transaction_type)
        .all()
    )
    totals = {kind: as_decimal(total) for kind, total in rows}
    income = totals.get("income", Decimal("0"))
    expense = totals.get("expense", Decimal("0"))
    return {
        "income": income,
        "expense": expense,
        "net": income - expense,
        "savings_rate": (float((income - expense) / income * 100) if income > 0 else 0.0),
    }


def category_spending(year=None, month=None):
    start, end = month_bounds(year, month)
    rows = (
        db.session.query(Category.name, Category.icon, func.sum(Transaction.amount))
        .join(Transaction, Transaction.category_id == Category.id)
        .filter(Transaction.transaction_type == "expense", Transaction.occurred_at >= start, Transaction.occurred_at < end)
        .group_by(Category.id, Category.name, Category.icon)
        .order_by(func.sum(Transaction.amount).desc())
        .all()
    )
    return [{"name": n, "icon": i, "amount": as_decimal(a)} for n, i, a in rows]


def daily_cashflow(days=14):
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    rows = (
        db.session.query(
            func.date(Transaction.occurred_at),
            func.sum(case((Transaction.transaction_type == "income", Transaction.amount), else_=0)),
            func.sum(case((Transaction.transaction_type == "expense", Transaction.amount), else_=0)),
        )
        .filter(Transaction.occurred_at >= start_dt, Transaction.occurred_at < end_dt)
        .group_by(func.date(Transaction.occurred_at))
        .all()
    )
    by_day = {str(day): (as_decimal(inc), as_decimal(exp)) for day, inc, exp in rows}
    labels, income, expense = [], [], []
    for offset in range(days):
        d = start_date + timedelta(days=offset)
        key = d.isoformat()
        inc, exp = by_day.get(key, (Decimal("0"), Decimal("0")))
        labels.append(d.strftime("%d %b"))
        income.append(float(inc))
        expense.append(float(exp))
    return {"labels": labels, "income": income, "expense": expense}


def budget_progress(year=None, month=None):
    start, end = month_bounds(year, month)
    year, month = start.year, start.month
    budgets = Budget.query.filter_by(year=year, month=month).all()
    spent_rows = (
        db.session.query(Transaction.category_id, func.sum(Transaction.amount))
        .filter(Transaction.transaction_type == "expense", Transaction.occurred_at >= start, Transaction.occurred_at < end)
        .group_by(Transaction.category_id)
        .all()
    )
    spent = {cid: as_decimal(amount) for cid, amount in spent_rows}
    result = []
    for budget in budgets:
        used = spent.get(budget.category_id, Decimal("0"))
        amount = as_decimal(budget.amount)
        result.append({
            "budget": budget,
            "spent": used,
            "remaining": amount - used,
            "percent": min(float((used / amount * 100) if amount else 0), 999),
        })
    return result


def save_attachment(file_storage, upload_root, transaction_id=None, attachment_type="receipt"):
    if not file_storage or not file_storage.filename:
        return None
    original = secure_filename(file_storage.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: .{ext or 'unknown'}")
    folder = "receipts" if attachment_type == "receipt" else "documents"
    target_dir = Path(upload_root) / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4().hex}.{ext}"
    full_path = target_dir / stored
    file_storage.save(full_path)
    attachment = Attachment(
        original_name=original,
        stored_name=stored,
        relative_path=f"{folder}/{stored}",
        mime_type=file_storage.mimetype,
        size_bytes=full_path.stat().st_size,
        attachment_type=attachment_type,
        transaction_id=transaction_id,
    )
    db.session.add(attachment)
    return attachment


def upcoming_bills(limit=6):
    return (
        Bill.query.filter(Bill.status == "unpaid")
        .order_by(Bill.due_date.asc())
        .limit(limit)
        .all()
    )


def find_category(name, kind):
    if not name:
        return None
    return Category.query.filter(func.lower(Category.name) == name.strip().lower(), Category.kind == kind).first()
