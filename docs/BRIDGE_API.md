# Bridge API

Everything a QML screen may call. Screens hold no business rules, SQL or money arithmetic.

## Conventions

**One global:** `api`, available in every QML file. Nothing else is exposed.

**Slots** return `{ok: true, data: ...}` or `{ok: false, error: "message for staff"}`, never throw.
Check `ok`, and show `error` with `MessageBanner.showResult(result)`. "Data" below means `result.data`.
All arguments are required: pass `""`, `0` or `{}` where a slot says so. With nobody signed in
every slot returns an error, except `session.signIn`, `session.createFirstManager`,
`session.signOut`, `settings.get`, `staff.roles`, `sales.paymentMethods` and `reports.localDate`.
Permissions are enforced in the services: a refused call returns `ok: false`.

**Typed text.** Money and quantity arguments are the text as typed (`"12.50"`, `"-1.5"`). Pass it
unchanged; the bridge parses it and invalid text comes back as an error. Never compute with them.

**Display strings** are added to every record, at any depth:

| Service key | Added | Example |
|---|---|---|
| `price_cents` (any `*_cents`) | `price_text`, `price_input` | `$1,234.50`, `1234.50` |
| `on_hand_milli` (any `*_milli`) | `on_hand_text`, `on_hand_input` | `1,500.25`, `1500.25` |
| `delta_milli` | `delta_text` is signed | `+2`, `-1.5` |
| `tax_rate_bp` | `tax_rate_text`, `tax_rate_input` | `8.25%`, `8.25` |
| `created_at` (any `*_at`) | `created_at_text`, in local time | `2026-11-05 14:30` |
| `voided_at` | also `voided` (bool) | |
| `role`, `payment_method`, `movement_type` | `role_text` etc. | `Stock clerk`, `Card`, `Void reversal` |

Show `*_text`. Pre-fill edit forms with `*_input`, which can be sent straight back.

**Null** arrives as `""`, so `row.void_reason` is always safe to bind. `active` is always a bool.

**Edit slots** take a map holding only the fields to change; an unknown field name is a bug and
returns the generic error (the log file names the field).

**After a session-ending slot.** Sign-in, sign-out, `backup.restore` and changes to your own account
destroy the current view on the *next* event-loop turn, so the handler that called the slot can
finish and use the result. The same goes for `session.goTo`.

## Signals

| Object | Signal | Fires when |
|---|---|---|
| `api` | `salesChanged()` | a sale is recorded or voided |
| `api` | `stockChanged()` | any stock movement, including sales and voids, or an ingredient edit |
| `api` | `menuChanged()` | a category, item or recipe changes |
| `api` | `staffChanged()` | a staff record changes |
| `api` | `restored()` | a backup replaced the database |
| `api.sales` | `cartChanged()` | the cart changes, including when it is discarded |
| `api.settings` | `changed()` | settings change |
| `api.session` | `changed()` | sign-in, sign-out, or the user's details change |
| `api.session` | `sectionChanged()` | the current section changes |
| `api.session` | `locked()` | the idle timeout signed the user out |

```qml
Connections {
    target: api
    function onStockChanged() { root.reload() }
}
```

## `api.session`

Properties, all bindable:

| Property | Type | Notes |
|---|---|---|
| `signedIn` | bool | |
| `needsFirstRun` | bool | no accounts exist yet |
| `user` | map | `{id, username, full_name, role, role_text}`; empty strings and id 0 when signed out |
| `permissions` | map | every permission name to a bool: `api.session.permissions.edit_menu` |
| `sections` | list | `[{key, label, view}]` this role may use |
| `currentSection`, `currentView` | string | section key and view file; `""` when signed out |
| `notice` | string | why the last session ended, shown on the sign-in screen |

Permission names: `record_sales`, `void_sales`, `view_menu`, `edit_menu`, `manage_stock`,
`view_sales_reports`, `view_stock_reports`, `manage_staff`, `manage_settings`, `view_audit_log`.
Use them to hide buttons; the services enforce them regardless.

| Slot | Data |
|---|---|
| `signIn(username, password)` | the user map |
| `createFirstManager(username, fullName, password, confirmPassword)` | the user map, now signed in |
| `signOut()` | `""` |
| `changePassword(current, new, confirmNew)` | `""` |
| `goTo(sectionKey)` | the key. Navigates: `api.session.goTo("inventory")` |

## `api.staff`

Staff record: `{id, username, full_name, role, role_text, active, created_at, created_at_text}`.

| Slot | Data |
|---|---|
| `roles()` | `[{value, label}]` |
| `listStaff()` | staff records, deactivated ones included |
| `createStaff(username, fullName, role, password, confirmPassword)` | record |
| `updateStaff(id, {full_name, role})` | record |
| `deactivateStaff(id)`, `reactivateStaff(id)` | record |
| `resetPassword(id, newPassword, confirmPassword)` | `""` |

Changing your own role or deactivating yourself signs you out.

## `api.settings`

Properties `restaurantName`, `currencySymbol`.

| Slot | Data |
|---|---|
| `get()` | `{restaurant_name, currency_symbol, tax_rate_bp, tax_rate_text, tax_rate_input}` |
| `update({restaurant_name, currency_symbol, tax_rate})` | same. `tax_rate` is a typed percentage: `"8.25"` |

## `api.backup`

Properties `folder` (path) and `folderUrl` (for a FileDialog's `currentFolder`).

| Slot | Data |
|---|---|
| `create()` | `{path}` |
| `restore(path)` | `{restored_from, safety_copy}`. Signs everyone out and reconnects. If it fails, nothing changed and the user stays signed in |

## `api.menu`

Category: `{id, name, sort_order, active}`.
Item: `{id, category_id, category_name, category_active, name, price_cents, price_text, price_input, active, has_recipe}`.
Mark items where `has_recipe` is false (FR-M4).
Recipe line: `{ingredient_id, ingredient_name, unit, qty_milli, qty_text, qty_input, ingredient_active}`.

| Slot | Data |
|---|---|
| `listCategories(includeInactive)` | categories |
| `createCategory(name, sortOrder)` | category. `sortOrder` is a number or typed text |
| `updateCategory(id, {name, sort_order})` | category |
| `setCategoryActive(id, active)` | category |
| `listItems(categoryId, includeInactive)` | items. `categoryId` 0 means all |
| `listSellableItems()` | active items in active categories |
| `getItem(id)` | item |
| `createItem(categoryId, name, price)` | item |
| `updateItem(id, {name, category_id, price})` | item |
| `setItemActive(id, active)` | item |
| `getRecipe(itemId)` | recipe lines; empty means no recipe |
| `setRecipe(itemId, [{ingredient_id, qty}])` | recipe lines. `qty` is typed text; `[]` clears it |

## `api.inventory`

Ingredient: `{id, name, unit, unit_cost_cents, unit_cost_text, unit_cost_input, on_hand_milli, on_hand_text, on_hand_input, low_milli, low_text, low_input, active}`.
Low-stock rows add `is_negative`.
Movement from a stock slot: `{id, ingredient_id, movement_type, movement_type_text, delta_milli, delta_text, delta_input, staff_id, sale_id, note, created_at, created_at_text}`.
`listMovements` rows add `ingredient_name, unit, username, full_name`.

| Slot | Data |
|---|---|
| `listIngredients(includeInactive)` | ingredients |
| `getIngredient(id)` | ingredient |
| `createIngredient(name, unit, unitCost, low)` | ingredient. Both are typed text and required |
| `updateIngredient(id, {name, unit, unit_cost, low})` | ingredient |
| `setIngredientActive(id, active)` | ingredient |
| `receiveStock(id, qty, note)` | movement |
| `recordWaste(id, qty, note)` | movement. `qty` is positive |
| `adjustStock(id, delta, note)` | movement. `delta` is signed |
| `recordCount(id, counted, note)` | movement, or `""` if the count matched |
| `listLowStock()` | ingredients at or below threshold, negative ones first |
| `lowStockCount()` | a number. Any role, for the home screen |
| `listMovements(ingredientId, dateFrom, dateTo, limit, offset)` | movements. `0` and `""` mean no filter. `limit` at most 1000 |

## `api.sales`

The cart lives in the bridge, which does its arithmetic, and is discarded on sign-out and on the idle lock.

Cart: `{lines, item_count, subtotal_text, tax_rate_text, tax_text, total_text}` (plus `_cents` and `_input` forms).
Line: `{menu_item_id, name, category, quantity, unit_price_text, line_total_text, available}`.
`available` is false if a manager deactivated the item meanwhile; checkout then refuses and names it.

Sale: `{id, staff_id, cashier_name, payment_method, payment_method_text, subtotal_text, tax_text, total_text, tax_rate_text, created_at_text, voided, voided_at_text, voided_by_name, void_reason, items}`.
Sale line (`getSale` and `checkout`): `{id, menu_item_id, name_as_sold, category_as_sold, unit_price_text, quantity, line_total_text}`.

| Slot | Data |
|---|---|
| `cart()` | cart |
| `cartAdd(itemId)` | cart. Adds one |
| `cartSetQuantity(itemId, quantity)` | cart. A number or typed text; 0 removes the line |
| `cartRemove(itemId)`, `cartClear()` | cart |
| `paymentMethods()` | `[{value, label}]` |
| `checkout(paymentMethod)` | sale. Empties the cart; on failure the cart is kept |
| `voidSale(id, reason)` | sale |
| `getSale(id)` | sale with `items` |
| `listSales(startDate, endDate, limit, offset)` | sales without `items`, newest first. Dates as `YYYY-MM-DD`, both or neither (`""`). `limit` at most 500. Cashiers see only their own last 7 days |

## `api.reports`

Report names: `sales_summary`, `sales_by_item`, `sales_by_category`, `sales_by_payment_method`,
`sales_by_cashier`, `movement_history` (these need dates), `stock_levels`, `stock_value`, `low_stock` (these don't).

`params` is `{start_date, end_date}` as `YYYY-MM-DD` for dated reports, `{}` for the others.
`movement_history` also accepts `ingredient_id`. Any other key is a bug.

| Slot | Data |
|---|---|
| `listReports()` | `[{name, title, dated}]` this role may run |
| `run(name, params)` | `{name, title, columns, rows, summary}` |
| `exportCsv(name, params, path)` | `{path, row_count}`. `path` may be a FileDialog URL and must end in `.csv`. `row_count` leaves out the stock value report's Total line |
| `localDate(daysFromToday)` | `YYYY-MM-DD`: `localDate(0)` is today, `localDate(-6)` a week's start |
| `openReport(name)` | the name. Goes to the Reports section on that report, if the role may run it |
| `takeRequestedReport()` | the name `openReport` asked for, or `""`. The Reports screen reads it once on load |

`columns` is `[{key, header, display, numeric}]`, and goes straight into `DataTable`:

```qml
DataTable { columns: report.columns; rows: report.rows }
```

`summary` is the totals row for `sales_summary`, `{total_value_cents, total_value_text, total_value_input}`
for `stock_value`, and `{}` otherwise. Stock level rows carry `status`: `OK`, `Low` or `Below zero`.

File picking uses QtQuick.Dialogs; pass the URL as text:

```qml
FileDialog {
    id: saveDialog
    fileMode: FileDialog.SaveFile
    defaultSuffix: "csv"
    nameFilters: ["CSV files (*.csv)"]
    onAccepted: banner.showResult(
        api.reports.exportCsv(name, params, selectedFile.toString()), "Exported.")
}
```

## `api.audit`

| Slot | Data |
|---|---|
| `listEntries(limit, offset)` | `[{id, action, details, created_at, created_at_text, staff_id, username}]`, newest first |

## Writing a screen

- Add the screen in `src/restaurant/qml/views/`. The root is an `Item` or layout with no
  `required` properties. The shell sizes it to the content area, 1016x688 at 1280x800, with padding
  already applied. It must look right at that size and larger.
- `import "../components"` as the last import, so that `Label` means the plain-text one in that folder,
  and use it rather than `Text`: stored data must never be read as markup, and a test checks this. Use
  `Theme` for every colour, size and font.
- Load data in `Component.onCompleted` and reload from the signals above. The view is destroyed when
  the user navigates away, and must tolerate `ok: false` for every role and when signed out, because
  `tests/test_qml_load.py` loads it in those states and fails on any QML warning.
- Add dialogs with `FormDialog` or `ConfirmDialog` inside the view; they centre themselves over the window.

### Components

Every entry works from `import "../components"`. Anything not listed below is an ordinary Qt property.

| Component | Use |
|---|---|
| `Label` | Qt's Label with `textFormat: Text.PlainText`, in place of Qt's own |
| `Theme` | singleton: colours, `spacingXs/S/M/L/Xl`, `controlHeight`, `touchHeight`, `rowHeight`, `radius`, `fontFamily`, `fontSmall/Body/Large/Title/Display` |
| `AppButton` | `kind`: `primary`, `secondary`, `danger`, `ghost`. `large: true` for touch-sized sales buttons |
| `AppTextField` | `label`, `text`, `placeholderText`, `errorText`, `echoMode`, `readOnly`, `large`; `accepted()`; `focusInput()` |
| `AppComboBox` | `label`, `model`, `textRole`, `valueRole`, `currentIndex`, `currentValue`, `currentText`; `activated(index)`; `selectValue(value)`. For `{value, label}` lists set `textRole: "label"; valueRole: "value"` |
| `MessageBanner` | `text`, `kind`: `error`, `success`, `warning`, `info`; `show(text, kind)`, `clear()`, `showResult(result, successText)` returns `result.ok`. Takes no space while empty |
| `FormDialog` | modal form: `title`, `confirmText`, `cancelText`, `danger`, `confirmEnabled`, `errorText`; `submitted()`; stays open until you call `close()`. Children go in a ColumnLayout |
| `ConfirmDialog` | `title`, `message`, `confirmText`, `danger`, `inputLabel` (requires typed text, such as a void reason); `confirmed(input)`; closes itself |
| `ChangePasswordDialog` | used by the shell |
| `Card` | bordered surface with a padded ColumnLayout: `padding` |
| `PageHeader` | `title`, `subtitle`; children sit on the right as actions |
| `DataTable` | `columns`, `rows`, `emptyText`, `clickable`; `rowClicked(row, index)`. A column may set `flex` for its share of the width |
| `DataRow` | one list row: `clickable`, `selected`, `clicked()`; children go in a RowLayout |
| `StatusChip` | `text`, `kind`: `neutral`, `success`, `warning`, `danger` |
| `NavButton`, `BrandPanel`, `PlaceholderContent` | used by the shell |

A new component must be added to `components/qmldir`; a test checks this.

## Screenshots

```
.venv\Scripts\python.exe tools\screenshot.py --view sales --role cashier --size 1280x800 --out C:\path\sales.png
```

| Option | Values | Default |
|---|---|---|
| `--view` | `first-run`, `login`, `home`, `sales`, `menu`, `inventory`, `reports`, `staff`, `settings`, `audit` | `home` |
| `--role` | `manager`, `cashier`, `stock_clerk` | `manager` |
| `--size` | `WIDTHxHEIGHT` | `1280x800` |
| `--out` | PNG path | `screenshot.png` |
| `--js` | a JS expression run in Main.qml's root scope before the capture, such as `changePasswordDialog.open()` | |

It builds a throwaway database, fills it through the services with menu items, stock (some low), sales
and a void, signs in as the role and navigates to the view. Exit code 1 and `QML warning:` lines on
stderr mean the screen has problems; a role that may not open a view exits with an error. View the PNG
with the Read tool.

It runs on Qt's `offscreen` platform with `QT_QUICK_BACKEND=software`. That platform ships no fonts and
renders blank text unless `QT_QPA_FONTDIR` names a font folder, so the tool points it at `C:\Windows\Fonts`.

## Tests

- `tests/test_bridge.py`: the bridge without screens. Every public slot on the bridge objects is found
  through Qt's meta-object and called signed out and with junk input for every role, so a new slot is covered.
- `tests/test_qml_load.py`: loads Main.qml and every file under `views/` and `components/` by glob, for
  each role and signed out, over demo data, and fails on any QML warning or error.

- `tests/test_stored_text_is_plain.py`: every label on every screen and dialog is plain text over data full
  of markup, and a local listener sees no requests.

## App files

`python -m restaurant`, whose code is in `bridge/app.py`, keeps everything in the data folder: `%APPDATA%\RestaurantManagement`, or
`RESTAURANT_DATA_DIR` if set. It holds `restaurant.db`, `backups\`, `restaurant.lock`, safety copies from
restores, and `restaurant.log`, where unexpected errors go with their tracebacks. Exit codes: 0 normal,
1 QML failed to load (or warned, under `--smoke-test`), 3 already running, 4 data folder unusable,
5 database unusable.
