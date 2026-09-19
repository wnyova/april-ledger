# April Ledger

**Your money, clearly mapped.**

A private, single-user personal finance dashboard for an Ubuntu Server on your local network. Built with Flask, MariaDB/MySQL, vanilla JavaScript, Chart.js, and a Telegram bot.

This guide uses **MariaDB** and **manual startup** for the website and bot. They remain stopped after a server reboot until you start them yourself.

## Features

- Dashboard with balances, income, expenses, spending charts, and yearly reports
- Income, expense, and transfers across bank, cash, e-wallet, and investment accounts
- Edit transactions with balance/report recalculation and preserved attachments
- Edit bills with protection for paid amounts and payment history
- Add, edit, archive, restore, and safely delete unused accounts
- Multiple credit cards, each with its own limit, outstanding debt, remaining limit, statement day, and due day
- Monthly budgets, bills, and recurring bill entries
- Receipt/document uploads and dark/light mode
- Telegram account selection, card payments, and summaries
- Database backup helper in `scripts/`

## Fresh installation

Use an Ubuntu release with Python 3.10 or newer, such as Ubuntu 24.04 LTS, and a regular user with `sudo` access. This guide uses `/opt/april-ledger`. Internet access is needed to download packages and communicate with GitHub and Telegram.

**Already installed?** Keep your existing database, `.env`, virtual environment, and uploads. Once your folder is connected to this repository, use [Update](#update). Do not repeat database creation or clone into an existing installation.

### 1. Install system packages

```bash
sudo apt update
sudo apt install -y git openssh-client python3 python3-venv python3-pip mariadb-server mariadb-client curl nano
sudo systemctl start mariadb
python3 --version
```

MariaDB is the database server used here. You do not need a separate MySQL server alongside it. The application's configuration names remain `MYSQL_*` when using MariaDB.

The app uses the server's local time for transaction dates. Check it with `timedatectl`. For Western Indonesia, set the timezone if needed:

```bash
sudo timedatectl set-timezone Asia/Jakarta
```

### 2. Set up GitHub SSH access

The Ubuntu user needs access to this private repository. If you already have working SSH access, reuse your existing key and substitute its path in the commands below.

Otherwise, create a dedicated key. Only run `ssh-keygen` if this filename does not already exist; do not overwrite an existing key:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -C "april-ledger-server" -f ~/.ssh/april_ledger_github
cat ~/.ssh/april_ledger_github.pub
```

Copy the printed **public** key. Open this GitHub repository's **Settings → Deploy keys → Add deploy key**, give it a name, paste the key, and leave **Allow write access** unchecked. Read access is enough to clone and pull. Keep the private key on the server. See [GitHub's deploy-key instructions](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys).

### 3. Clone the project

Use a new, empty installation directory:

```bash
sudo mkdir -p /opt/april-ledger
sudo chown "$(id -un):$(id -gn)" /opt/april-ledger
git -c core.sshCommand="ssh -i $HOME/.ssh/april_ledger_github -o IdentitiesOnly=yes" clone git@github.com:wnyova/april-ledger.git /opt/april-ledger
cd /opt/april-ledger
git config core.sshCommand "ssh -i $HOME/.ssh/april_ledger_github -o IdentitiesOnly=yes"
```

If SSH asks about a host fingerprint on first connection, verify it against [GitHub's published fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints) before accepting.

### 4. Create the database and application user

Open the MariaDB administrator console:

```bash
sudo mariadb
```

Run this SQL for a fresh database. Replace the password placeholder with your own password and keep it for step 5. A generated password without single quotes is simplest to paste into these examples.

```sql
CREATE DATABASE april_ledger CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'april_ledger'@'127.0.0.1' IDENTIFIED BY 'REPLACE_WITH_YOUR_DATABASE_PASSWORD';
GRANT ALL PRIVILEGES ON april_ledger.* TO 'april_ledger'@'127.0.0.1';
EXIT;
```

The database user and app configuration both use `127.0.0.1`. If the database/user already exists, reuse the existing credentials.

### 5. Install Python dependencies and configure the app

```bash
cd /opt/april-ledger
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp -n .env.example .env
chmod 600 .env
.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'
nano .env
```

Copy the generated secret into `SECRET_KEY`. Use the database password from step 4:

```dotenv
SECRET_KEY=PASTE_YOUR_GENERATED_SECRET_HERE
FLASK_ENV=production
HOST=0.0.0.0
PORT=8000

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=april_ledger
MYSQL_USER=april_ledger
MYSQL_PASSWORD='REPLACE_WITH_YOUR_DATABASE_PASSWORD'

CURRENCY=IDR
MAX_UPLOAD_MB=12

TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_ID=
```

Leave `DATABASE_URL` commented out unless you intentionally want it to override `MYSQL_*`. Telegram fields can stay empty until step 9. In nano, save with **Ctrl+O**, press **Enter**, then exit with **Ctrl+X**.

The `.env` file contains private credentials and is excluded from Git. Run the app as the directory owner so it can write uploads to `storage/`.

### 6. Initialize the database

```bash
cd /opt/april-ledger
.venv/bin/flask --app 'app:create_app()' init-db
.venv/bin/flask --app 'app:create_app()' seed
```

Expected messages: `Database tables created.` and `Starter data seeded.` The seed command adds starter categories and a Cash account. Add your banks, wallets, and cards in the website afterward. Skip `demo-data` for a real ledger because it creates sample transactions.

`init-db` creates missing tables; it does not migrate changes to existing table columns.

### 7. Start the website manually

```bash
cd /opt/april-ledger
.venv/bin/gunicorn --workers 2 --bind 0.0.0.0:8000 'app:create_app()'
```

Keep this terminal open. A `Listening at: http://0.0.0.0:8000` message means Gunicorn is listening. The quoted application factory follows the [Flask/Gunicorn documentation](https://flask.palletsprojects.com/en/stable/deploying/gunicorn/).

In another SSH terminal, find your server's LAN address and check the website locally:

```bash
hostname -I
curl -I http://127.0.0.1:8000
```

On a computer or phone connected to the same LAN, open **`http://SERVER_LAN_IP:8000`**, using the actual address, for example `http://192.168.1.50:8000`. Use HTTP. `0.0.0.0` is a listening address; `localhost` on your phone refers to the phone itself.

Check the firewall:

```bash
sudo ufw status
```

If UFW is active, allow your LAN subnet. For a network using `192.168.1.0/24`:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp
```

Replace that subnet if yours differs. This app has no web login and is intended for a trusted LAN; keep port 8000 off the public internet.

### 8. Add accounts and credit cards

Open **Accounts** and add each bank, wallet, and card separately. Opening balances represent the amount already present when you begin tracking.

For cards, set the name, institution, credit limit, opening debt, and optional last four digits, statement day, and due day. Three cards should be three accounts.

| Field | Example |
| --- | --- |
| Name | JeniusCC |
| Credit limit | 14500000 |
| Opening debt | 4594727 |
| Initial remaining limit | 9905273 |

**Opening debt is existing unpaid debt**, not the limit. It can include remaining installment principal already using your limit. Enter it once; do not also enter the same historical purchases as new expenses.

New card purchases count as expenses and increase debt. Payments are **transfers from your bank to the card**, reducing bank funds and card debt without counting the purchase twice. If installment principal was included in opening debt, its repayment is also a transfer. Record newly charged interest or fees separately as expenses.

**Amount to pay means total outstanding debt**, including opening debt. It is not a calculated monthly statement, minimum payment, or installment amount. Statement/due days are stored details; the current app does not generate bank statements or installment schedules. Use your bank statement for the amount due that month.

### 9. Configure Telegram

1. Open the official [BotFather](https://t.me/BotFather) and send `/newbot`.
2. Choose a display name and unique username ending in `bot`.
3. Copy the token into `.env` on the server and keep it private. See [Telegram's bot setup tutorial](https://core.telegram.org/bots/tutorial).
4. Get your **numeric Telegram user ID**, for example by sending `/start` to [userinfobot](https://t.me/userinfobot). This is a third-party ID helper; it does not need your bot token. Use your personal ID, not your username, phone number, or bot ID.
5. Edit these fields:

```bash
cd /opt/april-ledger
nano .env
```

```dotenv
TELEGRAM_BOT_TOKEN=PASTE_YOUR_BOTFATHER_TOKEN_HERE
TELEGRAM_ALLOWED_USER_ID=PASTE_YOUR_NUMERIC_USER_ID_HERE
```

Both values are required to start the bot. It accepts commands only from the configured user ID. Its dependencies were already installed in step 5.

### 10. Start and test the bot manually

Use a second terminal while the website runs in the first:

```bash
cd /opt/april-ledger
.venv/bin/python telegram_bot.py
```

Open your bot in Telegram, press **Start**, and send `/accounts`. You should receive your active database accounts. `/balance` also checks the connection without creating a transaction.

The bot uses long polling with outbound internet access. It does not need a public IP, incoming webhook, domain, tunnel, or router port forwarding. The web and bot processes share the database; the bot can work while the website process is stopped.

Run only **one bot instance**. The current code drops queued updates on startup, so send transactions while the bot is online. After editing `.env` while the bot runs, stop it with Ctrl+C and start it again to load the new values.

## Editing transactions and bills

Use the **Edit** link beside a row in **Transactions** or **Bills**. The form opens with the current values; choose **Save changes** to apply the correction or **Cancel** to leave it unchanged.

### Transactions

You can correct the type, amount, source account/card, transfer destination, category, date/time, and description. Edits update the existing transaction rather than creating another one. Account balances, credit-card debt and remaining limits, budgets, and reports are calculated from the corrected history.

Existing attachments and the original source (`web`, `telegram`, or `bill`) are preserved. Switching to a transfer clears the category; switching to income/expense clears the transfer destination. Transfers require two different accounts, and income/expense requires a matching category. Existing uncategorized entries (such as older bill payments) may keep their empty category when their transaction type stays the same. An existing archived account can be retained when correcting historical records; unrelated archived accounts cannot be selected.

### Bills

| Bill state | Editable fields |
| --- | --- |
| Unpaid | Name, amount, due date, recurrence, category, payment account, and note |
| Paid | Name, due date, and note only |

Saving an unpaid bill does not spend money or mark it paid. Changes affect only the selected bill, not other existing occurrences. Its next recurring occurrence is created when it is paid, using the values saved on that bill. **Mark paid + log expense** requires an active payment account; if none is set, edit the bill and choose one first.

For paid bills, the amount, account, category, recurrence, and payment status are locked on the server as well as in the form. This protects existing payment history and prevents accidentally logging another expense. To correct an actual payment, find the corresponding entry in **Transactions**. To change future payments, edit the next unpaid bill.

**Paid bills and their recorded expenses are separate records in the current database.** Editing a transaction does not automatically rewrite a paid bill, and editing a paid bill's name/note/due date does not rewrite its transaction or future bills. The app does not guess links between old records.

Invalid edits leave the original record unchanged and show an error while retaining your submitted values. If another tab changes or pays a record after you opened its edit form, saving is rejected; reload the edit page and review the latest values before trying again.

This feature adds no database columns and requires no schema migration or new Python dependencies. Web and bot startup remain manual.

## Telegram usage

These examples create real transactions. Create the referenced accounts first or substitute your own names:

```text
/spend 45000 Food #BCA nasi padang
/spend 219000 Entertainment #JeniusCC game
/income 8500000 Salary #BCA monthly salary
/paycc 1000000 #BCA #JeniusCC
```

Amounts are rupiah; enter plain digits. Put the category after the amount, then the account and description. Starter expense categories include `Food`, `Transport`, `Shopping`, `Entertainment`, `Subscription`, `Bills`, `Health`, `Travel`, and `Other`; income categories include `Salary` and `Bonus`.

Account aliases use **`#`**, match account names ignoring case, and replace spaces with underscores:

| Account name | Telegram alias |
| --- | --- |
| BCA | `#BCA` |
| BCACC | `#BCACC` |
| JeniusCC | `#JeniusCC` |
| Jenius CC | `#Jenius_CC` |
| UOB | `#UOB` |

`#Jenius_CC` matches `Jenius CC`, not `JeniusCC`. Saved-transaction replies name the bank, wallet, or card used.

Omit the account to choose it using buttons:

```text
/spend 45000 Food nasi padang
```

Finish or cancel that selection before sending another transaction. For a receipt, send a **photo** with this kind of caption:

```text
/spend 125000 Shopping #BCA groceries
```

Telegram uploads support photos; general document uploads are available through the website.

| Command | Purpose |
| --- | --- |
| `/start` | Command help |
| `/accounts` | Active accounts and credit-card details |
| `/balance` | Balances and debt totals |
| `/today` | Today's income and expenses |
| `/month` | Current month's summary |
| `/bills` | Upcoming unpaid bills |
| `/paycc AMOUNT #BANK #CARD` | Bank-to-card payment transfer |

`/bills` is an on-demand list. The current bot does not send scheduled bill reminders.

## Everyday manual startup and shutdown

After a reboot, start MariaDB if it is not already running:

```bash
sudo systemctl start mariadb
```

Start the website in one terminal:

```bash
cd /opt/april-ledger
.venv/bin/gunicorn --workers 2 --bind 0.0.0.0:8000 'app:create_app()'
```

Start the bot in another:

```bash
cd /opt/april-ledger
.venv/bin/python telegram_bot.py
```

Use **Ctrl+C** in each terminal to stop its process. Keep terminals open while using the app; closing SSH can stop its foreground process. This guide creates no startup services or scheduled jobs for the website or bot. MariaDB is a separate system service and may already start automatically according to Ubuntu's package defaults.

If an earlier setup created services called `april-ledger` and `april-ledger-bot`, disable their boot startup once:

```bash
sudo systemctl disable april-ledger april-ledger-bot
```

Only run that command if those services exist; substitute their actual names if different. Disabling boot startup does not stop a running service. Before switching an existing service to the foreground commands, stop it to avoid duplicate processes:

```bash
sudo systemctl stop april-ledger april-ledger-bot
```

## Update

```bash
cd /opt/april-ledger
git pull --ff-only
```

Pull updates repository files. It does not automatically start or restart the website or bot. A running Python process can continue using loaded code until the next manual start.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Git `Permission denied (publickey)` | The repository must have the public key, and Git must use its matching private key. Use the Ubuntu user who owns the key. |
| Database `Access denied` | Compare `.env` with the MariaDB credentials and `127.0.0.1` host. Check for a `DATABASE_URL` override. |
| Cannot connect to MariaDB | Check `sudo systemctl status mariadb`, `MYSQL_HOST`, and `MYSQL_PORT`. |
| Missing tables | Check database settings, then complete step 6 for a fresh installation. |
| Website connection refused | Check the Gunicorn terminal and `curl -I http://127.0.0.1:8000` on the server. |
| Local curl works; another device times out | Check the LAN IP, HTTP port 8000, UFW subnet rule, and Wi-Fi client isolation. |
| HTTP 500 | Read the traceback in the Gunicorn terminal for the application/database error. |
| `Address already in use` | Stop the earlier process listening on port 8000 before starting another. |
| Bot does not respond | Check its terminal, token, numeric allowed user ID, and internet access. Use the allowed Telegram account. |
| Telegram polling conflict | Stop duplicate bot instances, including old services or another server using the token. |
| Account not found | Check `/accounts`, spelling, spaces/underscores, and whether the account is archived. |
| Git refuses to pull because of local changes | Review `git status` and `git diff`. Keep local work; do not force an update with a hard reset. |

## Data and backups

Transactions and settings are stored in MariaDB. Uploaded files live under `storage/`. A database backup includes attachment metadata, not the uploaded files themselves. Preserve both the database and `storage/` when backing up or moving servers, and keep a private copy of `.env`.

Git excludes `.env`, `.venv/`, uploads, and database backups. Source code on GitHub is not a backup of your financial records. The helper under `scripts/` is provided separately; this guide does not schedule backups.

## Development checks

Run the regression suite from the repository root after installing `requirements.txt`:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The suite uses a separate, temporary SQLite database and temporary files; it does not connect to your configured MariaDB database. It covers balance/debt recalculation, transfers, reports, attachments, invalid and stale edits, paid-bill restrictions, and repeated bill payments. MariaDB/MySQL payment and edit routes also use row locks; SQLite tests do not exercise those database-specific concurrent locks.
