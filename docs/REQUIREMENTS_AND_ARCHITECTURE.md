# Restaurant Management: requirements and system architecture

## 1. Product

A Windows desktop app for **one restaurant on one shared PC**. Staff sign in with their own account to
take sales, keep the menu and recipes, track ingredient stock and read reports. It works offline.

**Qt/QML** for the interface, with **Python (PySide6)** behind it and **SQLite** as the database, since
one app on one PC needs no database server.

**Out of scope, not to be built:** table management, kitchen display, reservations, discounts, receipt
printing, card-terminal integration, online ordering, multi-PC use.

## 2. Roles

| Permission | Manager | Cashier | Stock clerk |
|---|---|---|---|
| Record sales | Yes | Yes | |
| Void sales | Yes | | |
| View the menu | Yes | Yes | Yes |
| Edit the menu | Yes | | |
| Manage stock | Yes | | Yes |
| Sales reports | Yes | | |
| Stock reports | Yes | | Yes |
| Manage staff | Yes | | |
| Settings, backup and restore | Yes | | |
| Audit log | Yes | | |

The table is `ROLE_PERMISSIONS` in `src/restaurant/core.py`. Every role can see the low-stock count on the
home screen, and change their own password. A cashier sees only their own sales from the last 7 days.

## 3. Functional requirements

### Staff and sign-in

- **FR-S1** Sign in with username and password.
- **FR-S2** On first launch with no accounts, the app asks for the first manager account. No default password anywhere.
- **FR-S3** Managers create, edit and deactivate staff. Staff are never deleted.
- **FR-S4** The last active manager cannot be deactivated or moved to another role.
- **FR-S5** Staff change their own password (current password required). Managers can reset anyone's.
- **FR-S6** The app locks after 10 minutes without input and requires signing in again.
- **FR-S7** The audit log records sign-ins, failed sign-ins, staff changes, price changes, stock adjustments and voids. Never passwords.

### Menu

- **FR-M1** Managers create, edit and deactivate menu categories and items.
- **FR-M2** Each item has a name, category, price and active flag. Only active items can be sold, and an item in an inactive category is not sellable either.
- **FR-M3** Each item can have a recipe: the quantity of each ingredient used per unit sold.
- **FR-M4** Items with no recipe can be sold and deduct no stock. The menu screen marks them.

### Inventory

- **FR-I1** Managers and stock clerks create and edit ingredients: name, unit, unit cost, low-stock threshold.
- **FR-I2** Every stock change is a movement (received, adjusted, wasted, sold, void_reversal), with user, time and optional note.
- **FR-I3** A stock count records the counted quantity and writes the difference as an adjustment. A zero difference writes no movement.
- **FR-I4** Ingredients at or below threshold appear in a low-stock list and as a count on the home screen.
- **FR-I5** A sale may take stock below zero, and negative levels are flagged in the low-stock list. Sales are never blocked by stock.

### Sales

- **FR-P1** Cashiers and managers build a sale from active menu items with quantities.
- **FR-P2** A sale records its payment method (cash, card, other), the cashier and the time.
- **FR-P3** Totals are item prices plus a manager-set tax rate. Prices exclude tax.
- **FR-P4** A sale stores each item's name, category and price as sold, so later menu changes don't alter past sales.
- **FR-P5** Saving a sale and deducting its stock happen in one transaction.
- **FR-P6** Managers void a sale with a required reason. The sale stays on record, is excluded from revenue and its stock is returned. A sale cannot be voided twice. The reversal mirrors the sale's actual `sold` movements, not the current recipe, which may have changed.

### Reports

- **FR-R1** Sales reports for a date range: totals, by item, by category, by payment method, by cashier. Voided sales are excluded and counted separately.
- **FR-R2** Stock reports: current levels, stock value at unit cost, low-stock items, movement history.
- **FR-R3** Any report can be exported to a CSV file.

## 4. Non-functional requirements

- **NFR-1** Windows 11, with no network or database server. Stored text is never interpreted as markup, so no data can make the app fetch anything.
- **NFR-2** One running copy. A second copy started at the same time is refused with a clear message.
- **NFR-3** Money in whole cents and stock in whole thousandths of a unit. No floats.
- **NFR-4** Foreign keys and checks enforced. Every multi-step change happens in one transaction.
- **NFR-5** Salted scrypt password hashes. Permissions enforced in services, not only by hiding buttons.
- **NFR-6** A dated backup on launch, with the last 7 kept. Managers can back up and restore on demand.
- **NFR-7** Screens work at 1280x800 and above, with touch-friendly controls on the sales screen.
- **NFR-8** Business rules tested without the UI. Automated tests load every QML screen.

## 5. Architecture

```
  QML screens                 src/restaurant/qml        what staff see; no business rules, no SQL
       |  api.<area>.<slot>(...) returns {ok, data} or {ok, error}
       v
  Bridge (PySide6 QObjects)   src/restaurant/bridge     the only Python importing Qt: session, idle
       |                                                lock, cart, startup, display strings
       v
  Services (plain Python)     src/restaurant/services   every business rule and permission check
       |
       v
  Data access                 src/restaurant/db         connection, transactions, migrations
       |
       v
  SQLite                      restaurant.db             constraints and triggers as the last defence
```

Dependencies only point down. `core.py` sits beside the services and holds what they share: error types,
roles and permissions, and money and quantity parsing. A test enforces that only `bridge/` imports Qt.

### Layers

**QML.** One file per screen under `qml/views`, with shared controls under `qml/components`. A screen calls
slots, shows the display strings it gets back and reloads on signals. It never does money arithmetic: the
cart's totals come from the bridge. Hiding a button is only a convenience, as the services enforce the
rule regardless. `docs/BRIDGE_API.md` is the reference for screen authors.

`components/Label.qml` replaces Qt's `Label` everywhere, because Qt's default treats text that looks like
markup as rich text. Without it, a stock note could hide words in the audit log, forge extra lines, or
fetch an image over the network. Tests check every label on every screen and dialog.

**Bridge.** `api` is the only object QML sees. `api_slot` turns each method into a slot that can't throw:
expected `AppError`s become their staff-facing message, and anything else is logged with its traceback and
replaced by a generic message. Arguments are never logged, because some are passwords. `to_qml` adds display
strings such as `price_text` to every record. `session.py` holds the signed-in user and the idle lock, which
an application-wide event filter resets on any input. `app.py` owns startup: the `QApplication`, the
single-copy lock, the launch backup and startup error messages.

**Services.** Module-level functions taking `(conn, actor, ...)`, which check the permission first and
return plain dicts. Expected problems raise an `AppError` subclass with a message fit to show staff. Every
write runs in `transaction(conn)`, which cannot nest, and audit entries join that same transaction.

| Service | Covers |
|---|---|
| `auth` | Password hashing, sign-in, first manager, changing your own password |
| `staff` | Staff records, the last-manager rule, password resets |
| `menu` | Categories, items, recipes |
| `inventory` | Ingredients, movements, stock counts, the low-stock list |
| `sales` | Recording and voiding sales, listing them, local dates to UTC bounds |
| `reports` | Sales and stock reports, CSV writing |
| `settings` | Restaurant name, currency symbol, tax rate |
| `backup` | Backup, pruning, restore and restore validation |
| `audit` | Writing and listing audit entries |

**Data access.** `connect()` opens SQLite with foreign keys on, WAL journaling and a busy timeout, and applies
any newer numbered migrations from `db/schema`. Applied migrations are never edited.

### Data model

```
  staff ---< sales ---< sale_items >--- menu_items >--- menu_categories
    |          |                            |
    |          |                            +---< recipe_items >--- ingredients
    |          |                                                        |
    +---< stock_movements >-----------------------------------------------+
    |          (sale_id links sold and void_reversal rows to their sale)
    +---< audit_log            settings (key, value)
```

- **Money is whole cents and stock whole thousandths of a unit,** in integer columns. Triggers refuse any
  non-integer value, because SQLite's column types and range checks alone would accept floats and text.
  Tax is in basis points, so 825 means 8.25%, and rounds half up on the sale's subtotal.
- **The stock ledger is the source of truth.** `stock_movements` is insert-only, enforced by triggers, and another
  trigger keeps `ingredients.on_hand_milli` equal to the sum of its movements. No service updates on-hand directly.
- **Sales are snapshots.** `sale_items` copies the name, category and price, and `sales` copies the tax rate.
  A void marks the sale and writes reversal movements that mirror its `sold` rows.
- **Times** are stored as UTC ISO-8601 text. Date ranges in reports are local calendar days converted to UTC
  bounds, so a daylight-saving day is correctly 23 or 25 hours.

### Key flows

**Recording a sale (FR-P1 to FR-P5).** Within one transaction, `record_sale` reads the prices, recipes and tax
rate from the database, never from the screen. It refuses unsellable items, inserts the sale and its snapshot
lines, then adds one `sold` movement per ingredient. Any failure rolls all of it back and keeps the cart.

**Voiding a sale (FR-P6).** Within one transaction, `void_sale` refuses a voided sale, marks this one, and
inserts a `void_reversal` for each of its `sold` movements. The recipe isn't consulted.

**Sign-in (FR-S1, NFR-5).** Passwords are stored as `scrypt$n$r$p$salt$hash`. Every failure path does one
scrypt hash and gives the same message, so neither timing nor wording reveals which usernames exist. An unknown
username isn't written to the audit log, because people sometimes type their password in that box.

**Backup and restore (NFR-6).** A backup uses SQLite's backup API, so it includes WAL contents, and is a single
standalone file. A restore works on a private copy of the chosen file, and in this order it checks:

1. The schema version is one this app knows, and the file's whole schema (every table, index and trigger,
   exact text included) is what the migrations create for that version. This refuses wrong columns, dropped
   triggers and added triggers, and happens first so that nothing from an unknown schema is ever run.
2. `integrity_check`, which also re-tests the stored rows against every `CHECK` and `NOT NULL` rule.
3. `foreign_key_check`, each column's values against its declared type, and on-hand against the ledger.
4. The required settings are present and usable, and an active manager with a usable password hash exists.

It then migrates that copy and notes the restore in its audit log, makes a safety copy of the current database,
and swaps the file in. A failure at any point leaves the current database as it was. The app then reconnects and
signs everyone out. A backup with no staff at all, such as the one made at the very first launch, is refused.

**CSV export (FR-R3).** Only `.csv` paths are written, so an export can't overwrite the database, a backup or the
log. Money and quantities are written as plain decimals, and text a spreadsheet would run as a formula is
prefixed with a single quote.

### Files on disk

Everything lives in the data folder: `%APPDATA%\RestaurantManagement`, or `RESTAURANT_DATA_DIR` if set. It
holds `restaurant.db`, `backups\`, restore safety copies, `restaurant.log` and `restaurant.lock`. `QLockFile`
provides the single-copy rule, and a lock left by a crashed copy is detected and taken over.

## 6. Requirement traceability

| Requirement | Implemented in | Tested in |
|---|---|---|
| FR-S1, FR-S2 | `services/auth.py`, `bridge/session.py`, `LoginView`, `FirstRunView` | `test_auth`, `test_qml_load` |
| FR-S3, FR-S4 | `services/staff.py`, `StaffView` | `test_staff`, `test_screens_management` |
| FR-S5 | `services/auth.py`, `services/staff.py`, `ChangePasswordDialog`, `StaffPasswordDialog` | `test_auth`, `test_staff`, `test_screens_management` |
| FR-S6 | `bridge/session.py` | `test_bridge`, `test_qml_load`, `test_screens_home_sales_reports` |
| FR-S7 | `services/audit.py`, called by each service; `AuditView` | `test_auth`, `test_staff`, `test_menu`, `test_inventory`, `test_sales`, `test_stored_text_is_plain` |
| FR-M1 to FR-M4 | `services/menu.py`, `MenuView` and its dialogs | `test_menu`, `test_screens_management` |
| FR-I1 to FR-I5 | `services/inventory.py`, the ledger triggers, `InventoryView`, `InventoryStockDialog`, `HomeView` | `test_inventory`, `test_foundation`, `test_screens_management` |
| FR-P1 to FR-P6 | `services/sales.py`, `bridge/sales.py`, the `Sales*` views | `test_sales`, `test_screens_home_sales_reports` |
| FR-R1 to FR-R3 | `services/reports.py`, `bridge/reports.py`, `ReportsView` | `test_reports`, `test_bridge`, `test_screens_home_sales_reports` |
| NFR-1 | Local SQLite only; `components/Label.qml` | `test_stored_text_is_plain` |
| NFR-2 | `bridge/app.py` (`QLockFile`) | `test_bridge` |
| NFR-3 | `core.py`, `002_whole_numbers.sql` | `test_foundation` |
| NFR-4 | `db/connection.py`, the schema | `test_foundation`, and rollback tests in each service's file |
| NFR-5 | `services/auth.py`, `core.require` in every service | `test_auth`, `test_bridge`, each service's tests |
| NFR-6 | `services/backup.py`, `bridge/app.py`, `SettingsView` | `test_backup`, `test_bridge` |
| NFR-7 | `qml/` layouts, `Theme.touchHeight` | `test_screens_*` run at 1280x800; screenshots from `tools/screenshot.py` |
| NFR-8 | `tests/` | `test_qml_load` loads every view and component for each role |

## 7. Testing

Tests run with pytest, on in-memory or temporary databases, and never touch real data. Service tests cover
behaviour, permissions for each role, edge cases and rollback on failure. Bridge tests find every slot through Qt's
meta-object and call it signed out and with junk input for each role, so a new slot is covered automatically. Screen
tests drive real windows on Qt's offscreen platform with real mouse events, and any QML warning fails them.

## 8. Decisions and known limits

- **Restore is strict by design.** A hand-edited database, or a backup with no usable manager, is refused rather than
  repaired. Anyone with such a file and a real need can still swap files by hand while the app is closed.
- **Stock value** treats negative on-hand as worth nothing, so one bad level can't reduce the total.
- **A count can't record below zero,** because physical stock can't be negative. On-hand itself can be, after a sale.
- **The app protects against staff using it, not against someone with the Windows account.** All staff share one
  Windows user, who can delete or copy the data folder directly. The database isn't encrypted.
- **Backups stay on the same PC.** Copying them elsewhere is a manual job, and the README says so.
- **One PC only.** SQLite in a user's profile with a single-copy lock is the deliberate design. Sharing between tills
  would need a different storage and locking design.
