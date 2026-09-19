"""Regression tests for edits that affect balances and bill payment history.

Run from the repository root: python -m unittest discover -s tests -v
Uses an isolated SQLite database and temporary upload directory.
"""
import re
import tempfile
import unittest
from types import SimpleNamespace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models import Account, Attachment, Bill, Category, Transaction
from app.services import account_balances, monthly_summary, budget_progress
from app.models import Budget


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(SimpleNamespace(**{
            'TESTING': True, 'SECRET_KEY': 'test-only',
            'SQLALCHEMY_DATABASE_URI': 'sqlite://',
            'SQLALCHEMY_TRACK_MODIFICATIONS': False,
            'UPLOAD_ROOT': self.tmp.name,
        }))
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        self.bank = Account(name='Bank', account_type='bank', opening_balance=1000000)
        self.card = Account(name='Card', account_type='credit', opening_balance=200000, credit_limit=500000)
        self.other = Account(name='Other bank', account_type='bank', opening_balance=300000)
        self.archived = Account(name='Archived', account_type='bank', opening_balance=0, is_active=False)
        self.food = Category(name='Food', kind='expense')
        self.shopping = Category(name='Shopping', kind='expense')
        self.salary = Category(name='Salary', kind='income')
        db.session.add_all([self.bank, self.card, self.other, self.archived, self.food, self.shopping, self.salary])
        db.session.commit()
        self.tx = Transaction(occurred_at=datetime(2026, 9, 18, 12, 34, 56),
                              transaction_type='expense', amount=50000,
                              account=self.bank, category=self.food,
                              description='Lunch', source='telegram')
        self.bill = Bill(name='Internet', amount=100000, due_date=date(2026, 1, 31),
                         recurrence='monthly', account=self.bank, category=self.food, note='Original')
        db.session.add_all([self.tx, self.bill])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.tmp.cleanup()

    def revision(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        match = re.search(r'name="revision" value="([a-f0-9]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match, 'Edit forms must carry the record revision')
        return match.group(1)

    def tx_form(self, **changes):
        data = dict(transaction_type='expense', amount='50000.00', account_id=str(self.bank.id),
                    category_id=str(self.food.id), destination_account_id='',
                    occurred_at='2026-09-18T12:34:56', description='Lunch',
                    revision=self.revision(f'/transactions/{self.tx.id}/edit'))
        data.update(changes)
        return data

    def bill_form(self, **changes):
        data = dict(name='Internet', amount='100000.00', due_date='2026-01-31', recurrence='monthly',
                    account_id=str(self.bank.id), category_id=str(self.food.id), note='Original',
                    revision=self.revision(f'/bills/{self.bill.id}/edit'))
        data.update(changes)
        return data

    def edit_tx(self, data):
        return self.client.post(f'/transactions/{self.tx.id}/edit', data=data)

    def edit_bill(self, data):
        return self.client.post(f'/bills/{self.bill.id}/edit', data=data)

    def test_edit_moves_expense_to_card_without_duplicate_and_updates_reports(self):
        db.session.add(Budget(year=2026, month=9, amount=100000, category=self.shopping))
        db.session.commit()
        response = self.edit_tx(self.tx_form(amount='75000', account_id=str(self.card.id),
                                            category_id=str(self.shopping.id)))
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual(Transaction.query.count(), 1)
        self.assertEqual(account_balances()[1][self.bank.id], Decimal('1000000'))
        self.assertEqual(account_balances()[1][self.card.id], Decimal('275000'))
        self.assertEqual(monthly_summary(2026, 9)['expense'], Decimal('75000'))
        self.assertEqual(budget_progress(2026, 9)[0]['spent'], Decimal('75000'))

    def test_edit_preserves_source_attachment_and_timestamp_seconds(self):
        path = Path(self.tmp.name) / 'receipt.txt'
        path.write_text('original receipt')
        attachment = Attachment(original_name='receipt.txt', stored_name='receipt.txt',
                                relative_path='receipt.txt', transaction=self.tx)
        db.session.add(attachment)
        db.session.commit()
        response = self.edit_tx(self.tx_form(description='Corrected lunch'))
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual(self.tx.source, 'telegram')
        self.assertEqual(self.tx.occurred_at, datetime(2026, 9, 18, 12, 34, 56))
        self.assertEqual(self.tx.attachments[0].id, attachment.id)
        self.assertEqual(path.read_text(), 'original receipt')

    def test_edit_to_transfer_clears_category_and_does_not_count_as_expense(self):
        response = self.edit_tx(self.tx_form(transaction_type='transfer', amount='125000',
                                            destination_account_id=str(self.card.id)))
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(self.tx.category_id)
        self.assertEqual(account_balances()[1][self.bank.id], Decimal('875000'))
        self.assertEqual(account_balances()[1][self.card.id], Decimal('75000'))
        self.assertEqual(monthly_summary(2026, 9)['expense'], 0)

    def test_transfer_to_income_clears_destination_and_restores_card_debt(self):
        self.tx.transaction_type = 'transfer'
        self.tx.destination_account = self.card
        self.tx.category = None
        db.session.commit()
        response = self.edit_tx(self.tx_form(transaction_type='income', category_id=str(self.salary.id),
                                            destination_account_id=str(self.card.id)))
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(self.tx.destination_account_id)
        self.assertEqual(account_balances()[1][self.bank.id], Decimal('1050000'))
        self.assertEqual(account_balances()[1][self.card.id], Decimal('200000'))
        self.assertEqual(monthly_summary(2026, 9)['income'], Decimal('50000'))

    def test_invalid_transaction_edits_leave_original_untouched(self):
        cases = [dict(amount=value) for value in ['0', '-1', 'NaN', 'Infinity', '0.0001', '10000000000000000', 'abc']]
        cases += [dict(transaction_type='unknown'), dict(account_id='99999'), dict(account_id='bad'),
                  dict(account_id=str(self.archived.id)), dict(category_id=str(self.salary.id)),
                  dict(category_id='99999'), dict(occurred_at=''), dict(occurred_at='2026-02-30T12:00'),
                  dict(transaction_type='transfer', destination_account_id=str(self.bank.id)),
                  dict(transaction_type='transfer', destination_account_id=str(self.archived.id)),
                  dict(transaction_type='transfer', destination_account_id='99999')]
        for change in cases:
            with self.subTest(change=change):
                response = self.edit_tx(self.tx_form(**change))
                self.assertEqual(response.status_code, 400)
                db.session.expire_all()
                self.assertEqual(self.tx.amount, Decimal('50000'))
                self.assertEqual(self.tx.account_id, self.bank.id)
                self.assertEqual(self.tx.description, 'Lunch')
                self.assertEqual(Transaction.query.count(), 1)

    def test_archived_original_account_can_be_kept_but_not_newly_selected(self):
        self.bank.is_active = False
        db.session.commit()
        response = self.edit_tx(self.tx_form(description='Historical correction'))
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual(self.tx.account_id, self.bank.id)
        self.assertFalse(self.bank.is_active)

    def test_stale_transaction_form_cannot_overwrite_newer_edit(self):
        data = self.tx_form(amount='70000')
        self.assertEqual(self.edit_tx(data).status_code, 302)
        data['amount'] = '80000'
        self.assertEqual(self.edit_tx(data).status_code, 409)
        db.session.expire_all()
        self.assertEqual(self.tx.amount, Decimal('70000'))

    def test_missing_revision_is_rejected(self):
        data = self.tx_form()
        data.pop('revision')
        self.assertEqual(self.edit_tx(data).status_code, 409)
        data = self.bill_form()
        data.pop('revision')
        self.assertEqual(self.edit_bill(data).status_code, 409)

    def test_edit_bill_changes_schedule_without_spending_or_creating_next_bill(self):
        response = self.edit_bill(self.bill_form(name='Wi-Fi', amount='150000', due_date='2026-02-28',
                                                recurrence='yearly', account_id=str(self.other.id), note='New plan'))
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual(self.bill.amount, Decimal('150000'))
        self.assertEqual(self.bill.account_id, self.other.id)
        self.assertEqual(self.bill.due_date, date(2026, 2, 28))
        self.assertEqual(self.bill.recurrence, 'yearly')
        self.assertEqual(self.bill.name, 'Wi-Fi')
        self.assertEqual(self.bill.status, 'unpaid')
        self.assertIsNone(self.bill.paid_at)
        self.assertEqual(Bill.query.count(), 1)
        self.assertEqual(Transaction.query.count(), 1)
        self.assertEqual(account_balances()[1][self.bank.id], Decimal('950000'))

    def test_invalid_bill_edits_are_atomic(self):
        for change in [dict(name=' '), dict(amount='0'), dict(amount='NaN'), dict(due_date='2026-02-30'),
                       dict(recurrence='weekly'), dict(account_id='99999'), dict(account_id='invalid'),
                       dict(account_id=str(self.archived.id)), dict(category_id=str(self.salary.id)),
                       dict(category_id='99999'), dict(category_id='invalid')]:
            with self.subTest(change=change):
                self.assertEqual(self.edit_bill(self.bill_form(**change)).status_code, 400)
                db.session.expire_all()
                self.assertEqual(self.bill.name, 'Internet')
                self.assertEqual(self.bill.amount, Decimal('100000'))
                self.assertEqual(self.bill.status, 'unpaid')

    def test_bill_optional_account_and_category_can_be_cleared(self):
        self.assertEqual(self.edit_bill(self.bill_form(account_id='', category_id='')).status_code, 302)
        db.session.expire_all()
        self.assertIsNone(self.bill.account_id)
        self.assertIsNone(self.bill.category_id)

    def test_stale_bill_form_cannot_overwrite_newer_edit(self):
        data = self.bill_form(amount='150000')
        self.assertEqual(self.edit_bill(data).status_code, 302)
        data['amount'] = '200000'
        self.assertEqual(self.edit_bill(data).status_code, 409)
        db.session.expire_all()
        self.assertEqual(self.bill.amount, Decimal('150000'))

    def test_pay_after_edit_uses_new_values_and_only_creates_one_successor(self):
        self.assertEqual(self.edit_bill(self.bill_form(amount='150000', account_id=str(self.card.id))).status_code, 302)
        for _ in range(2):
            response = self.client.post(f'/bills/{self.bill.id}/pay', data={'create_transaction': '1'})
            self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        payments = Transaction.query.filter_by(source='bill').all()
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0].amount, Decimal('150000'))
        self.assertEqual(payments[0].account_id, self.card.id)
        successor = Bill.query.filter_by(status='unpaid').one()
        self.assertEqual(successor.due_date, date(2026, 2, 28))
        self.assertEqual(successor.amount, Decimal('150000'))
        self.assertEqual(account_balances()[1][self.card.id], Decimal('350000'))

    def test_bill_without_payment_account_does_not_silently_become_paid(self):
        self.bill.account = None
        db.session.commit()
        self.client.post(f'/bills/{self.bill.id}/pay', data={'create_transaction': '1'})
        db.session.expire_all()
        self.assertEqual(self.bill.status, 'unpaid')
        self.assertIsNone(self.bill.paid_at)
        self.assertEqual(Bill.query.count(), 1)
        self.assertEqual(Transaction.query.count(), 1)

    def test_paid_bill_metadata_edit_keeps_payment_and_successor_untouched(self):
        self.client.post(f'/bills/{self.bill.id}/pay', data={'create_transaction': '1'})
        db.session.expire_all()
        paid_at = self.bill.paid_at
        self.assertEqual(self.edit_bill(self.bill_form(name='Internet corrected', note='Receipt checked')).status_code, 302)
        db.session.expire_all()
        self.assertEqual(self.bill.status, 'paid')
        self.assertEqual(self.bill.paid_at, paid_at)
        payment = Transaction.query.filter_by(source='bill').one()
        self.assertEqual(payment.amount, Decimal('100000'))
        self.assertEqual(payment.description, 'Internet')
        self.assertEqual(Bill.query.filter_by(status='unpaid').one().name, 'Internet')

    def test_paid_bill_financial_changes_and_status_reset_are_rejected(self):
        self.client.post(f'/bills/{self.bill.id}/pay', data={'create_transaction': '1'})
        for change in [dict(amount='200000'), dict(account_id=str(self.card.id)),
                       dict(category_id=str(self.shopping.id)), dict(recurrence='none'), dict(status='unpaid')]:
            with self.subTest(change=change):
                self.assertEqual(self.edit_bill(self.bill_form(**change)).status_code, 400)
                db.session.expire_all()
                self.assertEqual(self.bill.status, 'paid')
                self.assertEqual(self.bill.amount, Decimal('100000'))
                self.assertEqual(Transaction.query.filter_by(source='bill').count(), 1)
                self.assertEqual(Bill.query.count(), 2)

    def test_form_opened_before_payment_cannot_overwrite_paid_bill(self):
        data = self.bill_form(note='Old form')
        self.client.post(f'/bills/{self.bill.id}/pay', data={'create_transaction': '1'})
        self.assertEqual(self.edit_bill(data).status_code, 409)
        db.session.expire_all()
        self.assertEqual(self.bill.status, 'paid')
        self.assertEqual(self.bill.note, 'Original')

    def test_invalid_form_preserves_input_and_cancel_does_not_save(self):
        response = self.edit_tx(self.tx_form(amount='invalid', description='Keep my correction'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('value="Keep my correction"', response.get_data(as_text=True))
        self.client.get('/transactions')
        db.session.expire_all()
        self.assertEqual(self.tx.description, 'Lunch')

    def test_edit_transaction_date_moves_spending_between_months(self):
        response = self.edit_tx(self.tx_form(occurred_at='2026-10-01T00:00:00'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(monthly_summary(2026, 9)['expense'], 0)
        self.assertEqual(monthly_summary(2026, 10)['expense'], Decimal('50000'))

    def test_creation_still_works_with_shared_form_validation(self):
        data = self.tx_form(amount='45.000', description='Created via web',
                            destination_account_id=str(self.card.id))
        self.assertEqual(self.client.post('/transactions', data=data).status_code, 302)
        created = Transaction.query.filter_by(description='Created via web').one()
        self.assertEqual(created.amount, Decimal('45000'))
        self.assertIsNone(created.destination_account_id)
        self.assertEqual(created.source, 'web')
        data = self.bill_form(name='New bill', account_id='', category_id='')
        self.assertEqual(self.client.post('/bills', data=data).status_code, 302)
        self.assertEqual(Bill.query.filter_by(name='New bill').one().status, 'unpaid')

    def test_archived_payment_account_is_rejected_without_creating_expense(self):
        self.bank.is_active = False
        db.session.commit()
        self.client.post(f'/bills/{self.bill.id}/pay', data={'create_transaction': '1'})
        db.session.expire_all()
        self.assertEqual(self.bill.status, 'unpaid')
        self.assertEqual(Bill.query.count(), 1)
        self.assertEqual(Transaction.query.count(), 1)

    def test_uncategorized_payment_description_edit_preserves_category_and_budget(self):
        self.tx.category = None
        self.tx.source = 'bill'
        db.session.add(Budget(year=2026, month=9, amount=100000, category=self.food))
        db.session.commit()
        page = self.client.get(f'/transactions/{self.tx.id}/edit').get_data(as_text=True)
        response = self.edit_tx(self.tx_form(category_id='', description='Receipt checked'))
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(self.tx.category_id)
        self.assertEqual(self.tx.description, 'Receipt checked')
        self.assertIn('No category (keep existing)', page)
        self.assertEqual(budget_progress(2026, 9)[0]['spent'], 0)

    def test_blank_category_cannot_clear_existing_category_or_bypass_type_change(self):
        self.assertEqual(self.edit_tx(self.tx_form(category_id='')).status_code, 400)
        self.tx.category = None
        db.session.commit()
        self.assertEqual(self.edit_tx(self.tx_form(transaction_type='income', category_id='')).status_code, 400)

    def test_unknown_edit_ids_return_404(self):
        for path in ['/transactions/99999/edit', '/bills/99999/edit']:
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.post(path, data={}).status_code, 404)

    def test_lists_link_to_prefilled_edit_forms_and_escape_values(self):
        self.tx.description = '<script>alert(1)</script>'
        self.bill.note = '<img src=x onerror=alert(1)>'
        db.session.commit()
        self.assertIn(f'/transactions/{self.tx.id}/edit', self.client.get('/transactions').get_data(as_text=True))
        self.assertIn(f'/bills/{self.bill.id}/edit', self.client.get('/bills').get_data(as_text=True))
        tx_page = self.client.get(f'/transactions/{self.tx.id}/edit').get_data(as_text=True)
        self.assertIn('2026-09-18T12:34:56', tx_page)
        self.assertNotIn('<script>alert(1)</script>', tx_page)
        self.assertIn('&lt;script&gt;', tx_page)
        bill_page = self.client.get(f'/bills/{self.bill.id}/edit').get_data(as_text=True)
        self.assertIn('2026-01-31', bill_page)
        self.assertNotIn('<img src=x', bill_page)
        self.assertIn('&lt;img', bill_page)


if __name__ == '__main__':
    unittest.main()
