# April Ledger

**Your money, clearly mapped.**

April Ledger is a private, single-user personal finance dashboard built for a LAN-hosted Ubuntu Server.

## Included in v1.1

- Dashboard with balance, monthly income/expense/net and savings rate
- 14-day cash-flow chart and category spending chart
- Income, expense and transfer transactions
- Add, edit, archive, restore, and safely delete accounts
- Multi-account tracking for banks, cash, e-wallets, investments, and any number of credit cards
- Per-card credit limit, outstanding amount, remaining limit, statement day, due day, and last four digits
- Monthly budgets
- Bills and paid-to-expense logging
- Receipt/document uploads
- Yearly reports
- Dark/light mode
- Telegram account picker and explicit account aliases for every expense/income
- Telegram commands `/spend`, `/income`, `/accounts`, `/paycc`, `/balance`, `/today`, `/month`, `/bills`
- MySQL backup helper script

## Stack

- Python / Flask
- Flask-SQLAlchemy
- MySQL + PyMySQL
- Vanilla HTML/CSS/JavaScript
- Chart.js
- python-telegram-bot
- Gunicorn-ready

## Project layout

```text
april-ledger/
├── app/
│   ├── templates/
│   ├── static/
│   ├── models.py
│   ├── routes.py
│   └── services.py
├── storage/
├── backups/
├── scripts/
├── app.py
├── telegram_bot.py
├── config.py
├── requirements.txt
└── .env.example
```

## Notes

Installation is intentionally not covered here yet. The next step is to install this on Ubuntu Server, create the MySQL user/database, initialize the schema, and optionally configure Telegram + systemd services.

## Credit-card accounting

- A purchase made from a credit-card account is counted as an expense immediately and increases that card's outstanding amount.
- Paying a card is a transfer from a bank/e-wallet account to the credit-card account. It reduces cash and card debt, but does not count as another expense.
- Each physical credit card is stored as a separate account, so three cards have three independent limits and balances.

Telegram examples:

```text
/spend 45000 Food nasi padang
/spend 45000 Food #BCA nasi padang
/spend 219000 Entertainment #Jenius_CC game
/paycc 1000000 #BCA #Jenius_CC
/accounts
```

When `/spend` or `/income` omits `#Account`, the bot presents buttons for all active banks, wallets, and credit cards. Use underscores for account names containing spaces.
