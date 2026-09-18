from datetime import date, datetime
import calendar
from decimal import Decimal
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from sqlalchemy import func, or_

from .extensions import db
from .models import Account, Attachment, Bill, Budget, Category, Transaction
from .services import (
    account_balances,
    account_totals,
    budget_progress,
    category_spending,
    daily_cashflow,
    month_bounds,
    monthly_summary,
    parse_money,
    save_attachment,
    upcoming_bills,
)

bp = Blueprint("main", __name__)


def _parse_datetime(raw):
    if not raw:
        return datetime.now()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    raise ValueError("Invalid date/time")


def _to_int(raw, default=None):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _optional_money(raw):
    if raw is None or not str(raw).strip():
        return None
    return parse_money(raw)


def _account_values(form):
    account_type = (form.get("account_type") or "bank").lower()
    if account_type not in {"bank", "cash", "ewallet", "credit", "investment"}:
        raise ValueError("Invalid account type")

    opening = Decimal("0")
    if form.get("opening_balance"):
        opening_raw = form.get("opening_balance", "").strip()
        zero_probe = opening_raw.lower().replace("rp", "").replace(" ", "").replace(".", "").replace(",", "").replace("-", "").replace("+", "")
        if not zero_probe or set(zero_probe) == {"0"}:
            opening = Decimal("0")
        else:
            opening = parse_money(opening_raw.replace("-", ""))
            if opening_raw.startswith("-"):
                opening *= -1

    credit_limit = _optional_money(form.get("credit_limit")) if account_type == "credit" else None
    statement_day = _to_int(form.get("statement_day")) if account_type == "credit" else None
    due_day = _to_int(form.get("due_day")) if account_type == "credit" else None
    if statement_day is not None and not 1 <= statement_day <= 31:
        raise ValueError("Statement day must be 1-31")
    if due_day is not None and not 1 <= due_day <= 31:
        raise ValueError("Due day must be 1-31")

    last_four = (form.get("last_four") or "").strip()
    if last_four and (len(last_four) != 4 or not last_four.isdigit()):
        raise ValueError("Last four digits must contain exactly 4 numbers")

    return {
        "account_type": account_type,
        "institution": (form.get("institution") or "").strip()[:80] or None,
        "opening_balance": opening,
        "credit_limit": credit_limit,
        "statement_day": statement_day,
        "due_day": due_day,
        "last_four": last_four or None,
        "note": (form.get("note") or "").strip()[:255] or None,
    }


def _next_due_date(current, recurrence):
    if recurrence == "yearly":
        year = current.year + 1
        day = min(current.day, calendar.monthrange(year, current.month)[1])
        return date(year, current.month, day)
    if recurrence == "monthly":
        year = current.year + (1 if current.month == 12 else 0)
        month = 1 if current.month == 12 else current.month + 1
        day = min(current.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)
    return None


@bp.app_template_filter("money")
def money_filter(value):
    value = Decimal(value or 0)
    return "Rp {:,.0f}".format(value).replace(",", ".")


@bp.app_template_filter("filesize")
def filesize_filter(value):
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


@bp.app_context_processor
def inject_globals():
    return {"now": datetime.now(), "today": date.today()}


@bp.route("/")
def dashboard():
    summary = monthly_summary()
    accounts, balances = account_balances()
    totals = account_totals(accounts, balances)
    recent = Transaction.query.order_by(Transaction.occurred_at.desc()).limit(8).all()
    return render_template(
        "dashboard.html",
        page="dashboard",
        summary=summary,
        totals=totals,
        credit_accounts=[account for account in accounts if account.account_type == "credit"],
        balances=balances,
        recent=recent,
        bills=upcoming_bills(),
        budgets=budget_progress(),
    )


@bp.get("/api/dashboard")
def dashboard_api():
    summary = monthly_summary()
    accounts, balances = account_balances()
    totals = account_totals(accounts, balances)
    return jsonify({
        "summary": {k: float(v) if isinstance(v, Decimal) else v for k, v in summary.items()},
        "cashflow": daily_cashflow(14),
        "categories": [
            {"name": row["name"], "icon": row["icon"], "amount": float(row["amount"])}
            for row in category_spending()
        ],
        "accounts": [
            {"name": account.name, "type": account.account_type, "balance": float(balances.get(account.id, 0))}
            for account in accounts
        ],
        "account_totals": {key: float(value) for key, value in totals.items()},
    })


@bp.route("/transactions", methods=["GET", "POST"])
def transactions():
    if request.method == "POST":
        try:
            transaction_type = request.form.get("transaction_type", "expense").lower()
            if transaction_type not in {"income", "expense", "transfer"}:
                raise ValueError("Invalid transaction type")
            amount = parse_money(request.form.get("amount"))
            account_id = _to_int(request.form.get("account_id"))
            account = db.session.get(Account, account_id)
            if not account or not account.is_active:
                raise ValueError("Account is required")

            destination_id = _to_int(request.form.get("destination_account_id"))
            destination = db.session.get(Account, destination_id) if destination_id else None
            if transaction_type == "transfer":
                if not destination or not destination.is_active or destination.id == account.id:
                    raise ValueError("Transfer destination must be a different account")

            category = None
            category_id = _to_int(request.form.get("category_id"))
            if transaction_type != "transfer":
                category = db.session.get(Category, category_id) if category_id else None
                if not category or category.kind != transaction_type:
                    raise ValueError(f"Choose a valid {transaction_type} category")

            tx = Transaction(
                occurred_at=_parse_datetime(request.form.get("occurred_at")),
                transaction_type=transaction_type,
                amount=amount,
                description=(request.form.get("description") or "").strip()[:255] or None,
                source="web",
                account=account,
                destination_account=destination,
                category=category,
            )
            db.session.add(tx)
            db.session.flush()
            upload = request.files.get("attachment")
            if upload and upload.filename:
                save_attachment(upload, current_app.config["UPLOAD_ROOT"], transaction_id=tx.id)
            db.session.commit()
            flash("Transaction saved.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("main.transactions"))

    q = Transaction.query
    search = (request.args.get("q") or "").strip()
    tx_type = request.args.get("type")
    account_id = _to_int(request.args.get("account"))
    if search:
        q = q.filter(Transaction.description.ilike(f"%{search}%"))
    if tx_type in {"income", "expense", "transfer"}:
        q = q.filter(Transaction.transaction_type == tx_type)
    if account_id:
        q = q.filter(or_(Transaction.account_id == account_id, Transaction.destination_account_id == account_id))
    items = q.order_by(Transaction.occurred_at.desc()).limit(250).all()
    return render_template(
        "transactions.html",
        page="transactions",
        transactions=items,
        accounts=Account.query.order_by(Account.name).all(),
        active_accounts=Account.query.filter_by(is_active=True).order_by(Account.name).all(),
        categories=Category.query.order_by(Category.kind, Category.name).all(),
        search=search,
        selected_type=tx_type or "",
        selected_account=account_id,
    )


@bp.post("/transactions/<int:transaction_id>/delete")
def delete_transaction(transaction_id):
    tx = db.session.get(Transaction, transaction_id)
    if not tx:
        abort(404)
    root = Path(current_app.config["UPLOAD_ROOT"])
    for attachment in tx.attachments:
        path = root / attachment.relative_path
        if path.exists():
            path.unlink()
    db.session.delete(tx)
    db.session.commit()
    flash("Transaction deleted.", "success")
    return redirect(url_for("main.transactions"))


@bp.route("/accounts", methods=["GET", "POST"])
def accounts():
    if request.method == "POST":
        try:
            name = (request.form.get("name") or "").strip()[:80]
            if not name:
                raise ValueError("Account name is required")
            if Account.query.filter(func.lower(Account.name) == name.lower()).first():
                raise ValueError("Account name already exists")
            account = Account(name=name, **_account_values(request.form))
            db.session.add(account)
            db.session.commit()
            flash("Account added.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("main.accounts"))

    rows, balances = account_balances(include_inactive=True)
    return render_template("accounts.html", page="accounts", accounts=rows, balances=balances)


@bp.post("/accounts/<int:account_id>/edit")
def edit_account(account_id):
    account = db.session.get(Account, account_id)
    if not account:
        abort(404)
    try:
        name = (request.form.get("name") or "").strip()[:80]
        if not name:
            raise ValueError("Account name is required")
        duplicate = Account.query.filter(func.lower(Account.name) == name.lower(), Account.id != account.id).first()
        if duplicate:
            raise ValueError("Account name already exists")
        values = _account_values(request.form)
        has_history = Transaction.query.filter(
            or_(Transaction.account_id == account.id, Transaction.destination_account_id == account.id)
        ).first()
        if has_history and values["account_type"] != account.account_type:
            raise ValueError("Account type cannot be changed after it has transaction history")
        account.name = name
        for field, value in values.items():
            setattr(account, field, value)
        db.session.commit()
        flash("Account updated.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.accounts"))


@bp.post("/accounts/<int:account_id>/archive")
def archive_account(account_id):
    account = db.session.get(Account, account_id)
    if not account:
        abort(404)
    account.is_active = not account.is_active
    db.session.commit()
    flash("Account restored." if account.is_active else "Account archived.", "success")
    return redirect(url_for("main.accounts"))


@bp.post("/accounts/<int:account_id>/delete")
def delete_account(account_id):
    account = db.session.get(Account, account_id)
    if not account:
        abort(404)
    used = Transaction.query.filter(
        or_(Transaction.account_id == account.id, Transaction.destination_account_id == account.id)
    ).first() or Bill.query.filter_by(account_id=account.id).first()
    if used:
        flash("This account has history. Archive it instead so your reports stay accurate.", "error")
        return redirect(url_for("main.accounts"))
    db.session.delete(account)
    db.session.commit()
    flash("Unused account deleted.", "success")
    return redirect(url_for("main.accounts"))


@bp.route("/budgets", methods=["GET", "POST"])
def budgets():
    selected_year = _to_int(request.args.get("year"), date.today().year)
    selected_month = _to_int(request.args.get("month"), date.today().month)
    if request.method == "POST":
        try:
            year = _to_int(request.form.get("year"), date.today().year)
            month = _to_int(request.form.get("month"), date.today().month)
            category_id = _to_int(request.form.get("category_id"))
            amount = parse_money(request.form.get("amount"))
            category = db.session.get(Category, category_id)
            if not category or category.kind != "expense":
                raise ValueError("Choose an expense category")
            budget = Budget.query.filter_by(year=year, month=month, category_id=category_id).first()
            if budget:
                budget.amount = amount
            else:
                db.session.add(Budget(year=year, month=month, category=category, amount=amount))
            db.session.commit()
            flash("Budget saved.", "success")
            return redirect(url_for("main.budgets", year=year, month=month))
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("main.budgets"))

    return render_template(
        "budgets.html",
        page="budgets",
        budgets=budget_progress(selected_year, selected_month),
        categories=Category.query.filter_by(kind="expense").order_by(Category.name).all(),
        year=selected_year,
        month=selected_month,
    )


@bp.post("/budgets/<int:budget_id>/delete")
def delete_budget(budget_id):
    budget = db.session.get(Budget, budget_id)
    if budget:
        year, month = budget.year, budget.month
        db.session.delete(budget)
        db.session.commit()
        flash("Budget removed.", "success")
        return redirect(url_for("main.budgets", year=year, month=month))
    abort(404)


@bp.route("/bills", methods=["GET", "POST"])
def bills():
    if request.method == "POST":
        try:
            name = (request.form.get("name") or "").strip()[:120]
            if not name:
                raise ValueError("Bill name is required")
            amount = parse_money(request.form.get("amount"))
            due_date = datetime.strptime(request.form.get("due_date"), "%Y-%m-%d").date()
            bill = Bill(
                name=name,
                amount=amount,
                due_date=due_date,
                recurrence=request.form.get("recurrence", "none")[:20],
                category_id=_to_int(request.form.get("category_id")),
                account_id=_to_int(request.form.get("account_id")),
                note=(request.form.get("note") or "").strip()[:255] or None,
            )
            db.session.add(bill)
            db.session.commit()
            flash("Bill added.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("main.bills"))

    return render_template(
        "bills.html",
        page="bills",
        bills=Bill.query.order_by(Bill.status.asc(), Bill.due_date.asc()).all(),
        categories=Category.query.filter_by(kind="expense").order_by(Category.name).all(),
        accounts=Account.query.filter_by(is_active=True).order_by(Account.name).all(),
    )


@bp.post("/bills/<int:bill_id>/pay")
def pay_bill(bill_id):
    bill = db.session.get(Bill, bill_id)
    if not bill:
        abort(404)
    if bill.status != "paid":
        bill.status = "paid"
        bill.paid_at = datetime.now()
        if request.form.get("create_transaction") == "1" and bill.account_id:
            tx = Transaction(
                occurred_at=datetime.now(),
                transaction_type="expense",
                amount=bill.amount,
                description=bill.name,
                source="bill",
                account_id=bill.account_id,
                category_id=bill.category_id,
            )
            db.session.add(tx)
        next_due = _next_due_date(bill.due_date, bill.recurrence)
        if next_due:
            exists = Bill.query.filter_by(name=bill.name, due_date=next_due, status="unpaid").first()
            if not exists:
                db.session.add(Bill(
                    name=bill.name, amount=bill.amount, due_date=next_due, status="unpaid",
                    recurrence=bill.recurrence, category_id=bill.category_id, account_id=bill.account_id, note=bill.note
                ))
        db.session.commit()
        flash("Bill marked as paid.", "success")
    return redirect(url_for("main.bills"))


@bp.route("/reports")
def reports():
    return render_template("reports.html", page="reports")


@bp.get("/api/reports/monthly")
def reports_monthly_api():
    year = _to_int(request.args.get("year"), date.today().year)
    labels, income, expense, net = [], [], [], []
    for month in range(1, 13):
        summary = monthly_summary(year, month)
        labels.append(datetime(year, month, 1).strftime("%b"))
        income.append(float(summary["income"]))
        expense.append(float(summary["expense"]))
        net.append(float(summary["net"]))
    return jsonify({"year": year, "labels": labels, "income": income, "expense": expense, "net": net})


@bp.route("/files", methods=["GET", "POST"])
def files():
    if request.method == "POST":
        try:
            attachment_type = request.form.get("attachment_type", "document")
            saved = save_attachment(
                request.files.get("file"),
                current_app.config["UPLOAD_ROOT"],
                transaction_id=None,
                attachment_type=attachment_type,
            )
            if not saved:
                raise ValueError("Choose a file")
            db.session.commit()
            flash("File uploaded.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("main.files"))
    return render_template("files.html", page="files", files=Attachment.query.order_by(Attachment.created_at.desc()).all())


@bp.get("/files/<int:attachment_id>/download")
def download_file(attachment_id):
    attachment = db.session.get(Attachment, attachment_id)
    if not attachment:
        abort(404)
    root = Path(current_app.config["UPLOAD_ROOT"]).resolve()
    path = (root / attachment.relative_path).resolve()
    if root not in path.parents or not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=attachment.original_name)


@bp.post("/files/<int:attachment_id>/delete")
def delete_file(attachment_id):
    attachment = db.session.get(Attachment, attachment_id)
    if not attachment:
        abort(404)
    path = Path(current_app.config["UPLOAD_ROOT"]) / attachment.relative_path
    if path.exists():
        path.unlink()
    db.session.delete(attachment)
    db.session.commit()
    flash("File deleted.", "success")
    return redirect(url_for("main.files"))


@bp.route("/settings")
def settings():
    return render_template("settings.html", page="settings")
