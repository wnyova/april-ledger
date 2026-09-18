import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app import create_app
from app.extensions import db
from app.models import Account, Attachment, Bill, Category, Transaction
from app.services import account_balances, account_totals, monthly_summary, parse_money

flask_app = create_app()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()


def rupiah(value):
    return "Rp {:,.0f}".format(Decimal(value or 0)).replace(",", ".")


def allowed(update: Update):
    if not ALLOWED_USER_ID:
        return True
    return bool(update.effective_user and str(update.effective_user.id) == ALLOWED_USER_ID)


def find_category(name, kind):
    if name:
        category = Category.query.filter(func.lower(Category.name) == name.lower(), Category.kind == kind).first()
        if category:
            return category
    fallback = "Other Income" if kind == "income" else "Other"
    return Category.query.filter_by(name=fallback, kind=kind).first()


def find_account(alias):
    if not alias:
        return None
    normalized = alias.lstrip("#").replace("_", " ").strip().lower()
    return Account.query.filter(func.lower(Account.name) == normalized, Account.is_active.is_(True)).first()


def parse_entry(text, kind):
    # /spend 45000 Food #BCA nasi padang
    parts = text.split()
    if len(parts) < 2:
        command = "spend" if kind == "expense" else "income"
        raise ValueError(f"Usage: /{command} 45000 Food #BCA description")
    amount = parse_money(parts[1])
    category_name = parts[2] if len(parts) >= 3 and not parts[2].startswith("#") else None
    account_alias = next((part for part in parts[2:] if part.startswith("#")), None)
    description_parts = parts[3:] if category_name else parts[2:]
    description = " ".join(part for part in description_parts if part != account_alias) or None
    return amount, category_name, account_alias, description


def account_keyboard(accounts):
    rows = []
    for account in accounts:
        suffix = " · CC" if account.account_type == "credit" else ""
        rows.append([InlineKeyboardButton(f"{account.name}{suffix}", callback_data=f"entry_account:{account.id}")])
    rows.append([InlineKeyboardButton("Cancel", callback_data="entry_cancel")])
    return InlineKeyboardMarkup(rows)


def save_pending_entry(pending, account_id):
    account = db.session.get(Account, account_id)
    if not account or not account.is_active:
        raise ValueError("Account is no longer available")
    category = find_category(pending.get("category_name"), pending["kind"])
    tx = Transaction(
        occurred_at=datetime.now(),
        transaction_type=pending["kind"],
        amount=Decimal(pending["amount"]),
        account=account,
        category=category,
        description=pending.get("description"),
        source="telegram",
    )
    db.session.add(tx)
    db.session.flush()

    receipt_path = pending.get("receipt_path")
    if receipt_path:
        path = Path(receipt_path)
        db.session.add(Attachment(
            original_name="telegram-receipt.jpg",
            stored_name=path.name,
            relative_path=f"receipts/{path.name}",
            mime_type="image/jpeg",
            size_bytes=path.stat().st_size,
            attachment_type="receipt",
            transaction_id=tx.id,
        ))
    db.session.commit()
    return tx, account, category


def saved_message(tx, account, category):
    verb = "Charged to" if account.account_type == "credit" else ("Received in" if tx.transaction_type == "income" else "Paid with")
    return (
        f"Saved ✅\n{category.icon if category else ''} {category.name if category else tx.transaction_type.title()}\n"
        f"{rupiah(tx.amount)}\n{verb}: {account.name}\n{tx.description or '-'}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        "April Ledger ready.\n\n"
        "/spend 45000 Food nasi padang\n"
        "/spend 45000 Food #BCA nasi padang\n"
        "/income 8500000 Salary #BCA gaji\n"
        "/paycc 1000000 #BCA #Jenius_CC\n\n"
        "Also available: /accounts, /balance, /today, /month, /bills"
    )


async def add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, kind):
    if not allowed(update):
        return
    try:
        amount, category_name, account_alias, description = parse_entry(update.message.text or "", kind)
        pending = {"kind": kind, "amount": str(amount), "category_name": category_name, "description": description}
        with flask_app.app_context():
            account = find_account(account_alias)
            if account_alias and not account:
                raise ValueError(f"Account {account_alias} was not found. Use /accounts to see names.")
            accounts = Account.query.filter_by(is_active=True).order_by(Account.name).all()
            if not accounts:
                raise ValueError("No active accounts. Add one on the Accounts page first.")
            if account:
                tx, account, category = save_pending_entry(pending, account.id)
                message = saved_message(tx, account, category)
            else:
                context.user_data["pending_entry"] = pending
                keyboard = account_keyboard(accounts)
                message = None
        if message:
            await update.message.reply_text(message)
        else:
            await update.message.reply_text("Use which bank, wallet, or credit card?", reply_markup=keyboard)
    except Exception as exc:
        await update.message.reply_text(f"Could not save: {exc}")


async def spend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_entry(update, context, "expense")


async def income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_entry(update, context, "income")


async def choose_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    query = update.callback_query
    await query.answer()
    if query.data == "entry_cancel":
        pending = context.user_data.pop("pending_entry", None)
        if pending and pending.get("receipt_path"):
            Path(pending["receipt_path"]).unlink(missing_ok=True)
        await query.edit_message_text("Cancelled.")
        return
    pending = context.user_data.pop("pending_entry", None)
    if not pending:
        await query.edit_message_text("This entry expired. Send /spend or /income again.")
        return
    try:
        account_id = int(query.data.split(":", 1)[1])
        with flask_app.app_context():
            tx, account, category = save_pending_entry(pending, account_id)
            message = saved_message(tx, account, category)
        await query.edit_message_text(message)
    except Exception as exc:
        await query.edit_message_text(f"Could not save: {exc}")


async def accounts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    with flask_app.app_context():
        accounts, balances = account_balances()
        lines = []
        for account in accounts:
            balance = balances.get(account.id, Decimal("0"))
            if account.account_type == "credit":
                remaining = Decimal(account.credit_limit) - balance if account.credit_limit is not None else None
                lines.append(f"💳 {account.name}\n  To pay: {rupiah(balance)}\n  Remaining limit: {rupiah(remaining) if remaining is not None else 'not set'}")
            else:
                lines.append(f"🏦 {account.name}: {rupiah(balance)}")
    await update.message.reply_text("Accounts\n\n" + ("\n\n".join(lines) if lines else "None"))


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    with flask_app.app_context():
        accounts, balances = account_balances()
        totals = account_totals(accounts, balances)
    await update.message.reply_text(
        f"Financial position\nAssets: {rupiah(totals['assets'])}\n"
        f"Credit cards to pay: {rupiah(totals['credit_debt'])}\nNet: {rupiah(totals['net_worth'])}"
    )


async def paycc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    try:
        parts = (update.message.text or "").split()
        if len(parts) < 4:
            raise ValueError("Usage: /paycc 1000000 #BCA #Jenius_CC")
        amount = parse_money(parts[1])
        with flask_app.app_context():
            source = find_account(parts[2])
            card = find_account(parts[3])
            if not source or source.account_type == "credit":
                raise ValueError("The payment source must be an active bank, wallet, or cash account")
            if not card or card.account_type != "credit":
                raise ValueError("The destination must be an active credit card")
            db.session.add(Transaction(
                occurred_at=datetime.now(), transaction_type="transfer", amount=amount,
                account=source, destination_account=card,
                description=f"Credit card payment: {card.name}", source="telegram",
            ))
            db.session.commit()
        await update.message.reply_text(f"Credit-card payment saved ✅\n{rupiah(amount)}\n{source.name} → {card.name}")
    except Exception as exc:
        await update.message.reply_text(f"Could not save: {exc}")


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    start_at = datetime.combine(date.today(), time.min)
    end_at = start_at + timedelta(days=1)
    with flask_app.app_context():
        rows = Transaction.query.filter(Transaction.occurred_at >= start_at, Transaction.occurred_at < end_at).all()
        inc = sum((Decimal(x.amount) for x in rows if x.transaction_type == "income"), Decimal("0"))
        exp = sum((Decimal(x.amount) for x in rows if x.transaction_type == "expense"), Decimal("0"))
    await update.message.reply_text(f"Today\nIncome: {rupiah(inc)}\nExpense: {rupiah(exp)}\nNet: {rupiah(inc-exp)}")


async def month_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    with flask_app.app_context():
        summary = monthly_summary()
    await update.message.reply_text(
        f"This month\nIncome: {rupiah(summary['income'])}\nExpense: {rupiah(summary['expense'])}\n"
        f"Net: {rupiah(summary['net'])}\nSavings rate: {summary['savings_rate']:.1f}%"
    )


async def bills_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    with flask_app.app_context():
        bills = Bill.query.filter_by(status="unpaid").order_by(Bill.due_date.asc()).limit(10).all()
        lines = [f"{bill.due_date.strftime('%d %b')} — {bill.name}: {rupiah(bill.amount)}" for bill in bills]
    await update.message.reply_text("Upcoming bills\n" + ("\n".join(lines) if lines else "None 🎉"))


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update) or not update.message.photo:
        return
    caption = (update.message.caption or "").strip()
    if not caption.startswith(("/spend", "/income")):
        await update.message.reply_text("Send a receipt with caption like: /spend 125000 Shopping groceries")
        return
    kind = "expense" if caption.startswith("/spend") else "income"
    try:
        amount, category_name, account_alias, description = parse_entry(caption, kind)
        tg_file = await update.message.photo[-1].get_file()
        root = Path(flask_app.config["UPLOAD_ROOT"]) / "receipts"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{uuid4().hex}.jpg"
        await tg_file.download_to_drive(custom_path=str(path))
        pending = {
            "kind": kind, "amount": str(amount), "category_name": category_name,
            "description": description, "receipt_path": str(path),
        }
        with flask_app.app_context():
            account = find_account(account_alias)
            if account_alias and not account:
                raise ValueError(f"Account {account_alias} was not found")
            accounts = Account.query.filter_by(is_active=True).order_by(Account.name).all()
            if not accounts:
                path.unlink(missing_ok=True)
                raise ValueError("No active accounts. Add one on the Accounts page first.")
            if account:
                tx, account, category = save_pending_entry(pending, account.id)
                message = saved_message(tx, account, category) + "\nReceipt attached."
            else:
                context.user_data["pending_entry"] = pending
                keyboard = account_keyboard(accounts)
                message = None
        if message:
            await update.message.reply_text(message)
        else:
            await update.message.reply_text("Receipt ready. Use which account?", reply_markup=keyboard)
    except Exception as exc:
        await update.message.reply_text(f"Could not save: {exc}")


def main():
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is empty in .env")
    if not ALLOWED_USER_ID:
        raise SystemExit("TELEGRAM_ALLOWED_USER_ID is required for a private bot")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("spend", spend))
    app.add_handler(CommandHandler("income", income))
    app.add_handler(CommandHandler("accounts", accounts_cmd))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("paycc", paycc))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("month", month_cmd))
    app.add_handler(CommandHandler("bills", bills_cmd))
    app.add_handler(CallbackQueryHandler(choose_account, pattern=r"^entry_(account:|cancel)"))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
