from datetime import date, datetime, timedelta
from decimal import Decimal

import click

from .extensions import db
from .models import Account, Bill, Budget, Category, Transaction


def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Create all database tables."""
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("seed")
    def seed():
        """Seed sensible starter categories and a Cash account."""
        categories = [
            ("Salary", "income", "💰"),
            ("Bonus", "income", "✨"),
            ("Other Income", "income", "↗"),
            ("Food", "expense", "🍜"),
            ("Transport", "expense", "🛵"),
            ("Shopping", "expense", "🛍️"),
            ("Entertainment", "expense", "🎮"),
            ("Subscription", "expense", "📺"),
            ("Bills", "expense", "🧾"),
            ("Health", "expense", "💊"),
            ("Travel", "expense", "✈️"),
            ("Other", "expense", "•"),
        ]
        for name, kind, icon in categories:
            exists = Category.query.filter_by(name=name, kind=kind).first()
            if not exists:
                db.session.add(Category(name=name, kind=kind, icon=icon, is_system=True))
        if not Account.query.filter_by(name="Cash").first():
            db.session.add(Account(name="Cash", account_type="cash", opening_balance=0))
        db.session.commit()
        click.echo("Starter data seeded.")

    @app.cli.command("demo-data")
    def demo_data():
        """Optional demo records to preview the dashboard."""
        if Transaction.query.count() > 0:
            click.echo("Transactions already exist; demo data skipped.")
            return
        account = Account.query.first()
        if not account:
            click.echo("Run 'flask seed' first.")
            return
        salary = Category.query.filter_by(name="Salary", kind="income").first()
        food = Category.query.filter_by(name="Food", kind="expense").first()
        gaming = Category.query.filter_by(name="Entertainment", kind="expense").first()
        bills = Category.query.filter_by(name="Bills", kind="expense").first()
        now = datetime.now()
        db.session.add_all([
            Transaction(occurred_at=now - timedelta(days=8), transaction_type="income", amount=8500000, category=salary, account=account, description="Monthly salary", source="demo"),
            Transaction(occurred_at=now - timedelta(days=6), transaction_type="expense", amount=145000, category=food, account=account, description="Dinner", source="demo"),
            Transaction(occurred_at=now - timedelta(days=4), transaction_type="expense", amount=219000, category=gaming, account=account, description="Game purchase", source="demo"),
            Transaction(occurred_at=now - timedelta(days=2), transaction_type="expense", amount=499000, category=bills, account=account, description="Internet", source="demo"),
        ])
        db.session.commit()
        click.echo("Demo data added.")
