# Restaurant Management

A Windows desktop app for running one restaurant from one shared PC: taking sales, managing the
menu and recipes, tracking ingredient stock, reporting, and controlling who can do what. Its screens
are written in Qt/QML, its logic in Python (PySide6), and it keeps its data in a local SQLite
database. It needs no network and no database server.

**One PC only.** The database is a file in one Windows user's profile, and a second copy of the app
started at the same time is refused. It can't be shared between tills or used over a network.

It has three roles: **manager** (everything), **cashier** (sales and viewing the menu) and
**stock clerk** (stock, stock reports and viewing the menu). It deliberately has no table management,
kitchen display, reservations, discounts, receipt printing, card-terminal link or online ordering.

For what it must do and how it is built, see
[docs/REQUIREMENTS_AND_ARCHITECTURE.md](docs/REQUIREMENTS_AND_ARCHITECTURE.md).

## Set up on Windows 11, from a fresh clone

You need [Python 3.12 or newer](https://www.python.org/downloads/windows/) and Git. These commands are
for PowerShell. The install downloads PySide6 and needs internet once; the app itself never does.

```powershell
git clone <repository URL> restaurant_management
cd restaurant_management
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The commands here call the virtual environment's Python directly, so you don't need to activate it.

## Run it

```powershell
.venv\Scripts\python.exe -m restaurant
```

`.venv\Scripts\restaurant-management.exe` starts the same app without a console window, which suits a
desktop shortcut.

On first launch it asks you to create the first manager account. There is no default password. That
manager then adds the other staff under **Staff**, the tax rate under **Settings**, and the menu and
ingredients under **Menu** and **Inventory**.

The app signs out after 10 minutes without input.

## Try it with demo data

`tools/seed_demo.py` fills a new database with a small restaurant: menu, recipes, stock, one account
per role and four weeks of sales, voids and stock movements. Keep it apart from real data by
giving the demo its own folder:

```powershell
.venv\Scripts\python.exe tools\seed_demo.py demo-data\restaurant.db
$env:RESTAURANT_DATA_DIR = "$PWD\demo-data"
.venv\Scripts\python.exe -m restaurant
```

The tool prints the three usernames and their passwords, which are new each run and shown only once.
If you lose them, delete the `demo-data` folder and run it again.

`RESTAURANT_DATA_DIR` lasts until that PowerShell window closes, or run
`Remove-Item Env:RESTAURANT_DATA_DIR` to return to the real data. The tool refuses any database that
already has staff, so it can't overwrite a real restaurant's data.

## Run the tests

```powershell
.venv\Scripts\python.exe -m pytest
```

The full suite takes about five minutes, and shows no windows because it uses Qt's offscreen platform.
It tests the business rules without the UI, and loads and drives every QML screen. For a quick check
that the installed app starts, without affecting real data:

```powershell
$env:RESTAURANT_DATA_DIR = "$env:TEMP\restaurant-smoke-test"
.venv\Scripts\python.exe -m restaurant --smoke-test
Remove-Item Env:RESTAURANT_DATA_DIR
```

It exits with code 0 when everything loaded (`$LASTEXITCODE` shows it in PowerShell).

## Where the data lives

Everything is in the data folder: `%APPDATA%\RestaurantManagement`, or the folder named by
`RESTAURANT_DATA_DIR`.

| File | What it is |
|---|---|
| `restaurant.db` (with `-wal` and `-shm` beside it while running) | The database |
| `backups\restaurant-backup-YYYYMMDD-HHMMSS.db` | Dated backups. One is made at every launch, and the newest 7 are kept |
| `restaurant-pre-restore-YYYYMMDD-HHMMSS.db` | A safety copy of the database, made before each restore and never deleted automatically |
| `restaurant.log` | Unexpected errors, for diagnosis. It never holds passwords |
| `restaurant.lock` | Marks a running copy |

Managers can **Back up now** and **Restore from a backup** under **Settings**. A restore checks the chosen file
thoroughly and refuses anything that isn't an intact backup from this app with an active manager in it.

Backups stay on this PC, so copy them somewhere else regularly: if the PC's disk fails, they go with it.
If the database itself is damaged, the app explains at launch how to replace it with a backup.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Normal |
| 1 | The screens failed to load (or warned, under `--smoke-test`) |
| 3 | Another copy is already running |
| 4 | The data folder can't be used |
| 5 | The database can't be used |

## Project layout

```
src/restaurant/
  core.py         errors, roles and permissions, money and quantity parsing
  db/             connection, transactions, numbered schema migrations
  services/       the business rules: plain Python, no Qt
  bridge/         PySide6 objects that QML calls, and application startup
  qml/            the screens, with no business rules or SQL
tests/            pytest suite
tools/            seed_demo.py (demo data), screenshot.py (PNG of any screen)
docs/             requirements and architecture, and the bridge API for screen authors
```

To save a picture of a screen without touching any real data:

```powershell
.venv\Scripts\python.exe tools\screenshot.py --view sales --role cashier --out sales.png
```
