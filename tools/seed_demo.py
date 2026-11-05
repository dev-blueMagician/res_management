"""Fill an empty database with a small restaurant's demo data.

    python tools\\seed_demo.py demo-data\\restaurant.db [--days 28]

Everything goes through the real services, with a fixed random seed. The passwords are new
each run, printed once and stored only as hashes. A database that already has staff is
refused, so this can't touch a real restaurant's data.

How the past is written: the services take the time from ``utc_now``, and while seeding
each service module's ``utc_now`` is swapped for a simulated clock (``_services_use``),
which is moved forward event by event. No row is edited afterwards. Sales, their stock
movements, voids and audit entries are therefore written just as they would have been on
that day: totals come from record_sale and on-hand from the ledger trigger.
``check_consistency`` proves it before the passwords are printed.
"""

from __future__ import annotations

import argparse
import random
import secrets
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from restaurant.core import (  # noqa: E402
    Actor,
    AuthenticationError,
    Role,
    calculate_tax_cents,
    format_money,
    format_quantity,
    parse_money,
    parse_quantity,
)
from restaurant.db.connection import connect  # noqa: E402
from restaurant.services import audit, auth, inventory, menu, sales, settings, staff  # noqa: E402

SEED = 20260105
DEFAULT_DAYS = 28
EXIT_REFUSED = 2
RESTAURANT_NAME = "Olive & Thyme"
TAX_RATE_BP = 825

# name, unit, unit cost, low-stock level, level each delivery tops up to
INGREDIENTS = (
    ("Pizza flour", "kg", "1.20", "8", "45"),
    ("Tomato sauce", "L", "3.40", "5", "30"),
    ("Mozzarella", "kg", "9.80", "4", "20"),
    ("Fresh basil", "bunch", "1.75", "3", "14"),
    ("Olive oil", "L", "8.50", "2", "8"),
    ("Dried pasta", "kg", "2.30", "4", "20"),
    ("Ground beef", "kg", "11.20", "3", "12"),
    ("Parmesan", "kg", "17.00", "1", "5"),
    ("Romaine lettuce", "head", "1.40", "4", "18"),
    ("Chicken breast", "kg", "8.90", "3", "12"),
    ("Coffee beans", "kg", "14.50", "1", "5"),
    ("Milk", "L", "1.10", "6", "30"),
    ("Mascarpone", "kg", "7.60", "1", "5"),
    ("Lemons", "each", "0.35", "6", "30"),
)
# These deliveries stop this many days before the end, leaving low and negative stock to find.
LATE_DELIVERIES = {"Fresh basil": 8, "Lemons": 8}

# category: (item, price, how often it is ordered, recipe)
MENU = {
    "Pizza": (
        ("Margherita", "11.50", 12, {"Pizza flour": "0.25", "Tomato sauce": "0.12", "Mozzarella": "0.125", "Fresh basil": "0.1", "Olive oil": "0.01"}),
        ("Marinara", "9.50", 5, {"Pizza flour": "0.25", "Tomato sauce": "0.15", "Olive oil": "0.015"}),
        ("Quattro Formaggi", "13.75", 6, {"Pizza flour": "0.25", "Mozzarella": "0.2", "Parmesan": "0.04"}),
        ("Pollo", "13.25", 6, {"Pizza flour": "0.25", "Tomato sauce": "0.12", "Mozzarella": "0.125", "Chicken breast": "0.1"}),
    ),
    "Pasta": (
        ("Penne Pomodoro", "10.25", 7, {"Dried pasta": "0.12", "Tomato sauce": "0.2", "Fresh basil": "0.05"}),
        ("Tagliatelle Ragu", "12.90", 8, {"Dried pasta": "0.12", "Ground beef": "0.15", "Tomato sauce": "0.1", "Parmesan": "0.015"}),
        ("Lasagne", "13.50", 6, {"Dried pasta": "0.1", "Ground beef": "0.15", "Tomato sauce": "0.12", "Mozzarella": "0.08"}),
    ),
    "Salads": (
        ("Caesar Salad", "9.75", 5, {"Romaine lettuce": "0.5", "Chicken breast": "0.08", "Parmesan": "0.02", "Olive oil": "0.02"}),
        ("Caprese", "8.90", 3, {"Mozzarella": "0.1", "Fresh basil": "0.1", "Olive oil": "0.02"}),
    ),
    "Drinks": (
        ("Espresso", "2.80", 8, {"Coffee beans": "0.018"}),
        ("Cappuccino", "3.60", 7, {"Coffee beans": "0.018", "Milk": "0.15"}),
        ("Lemonade", "3.20", 6, {"Lemons": "1.5"}),
        ("Sparkling Water", "2.50", 9, {}),  # no recipe: bought in, not tracked as stock
        ("House Wine", "5.50", 7, {}),
    ),
    "Desserts": (
        ("Tiramisu", "6.25", 6, {"Mascarpone": "0.08", "Coffee beans": "0.006"}),
        ("Panna Cotta", "5.75", 3, {}),
    ),
    "Summer Specials": (
        ("Peach Salad", "9.25", 3, {"Romaine lettuce": "0.3", "Olive oil": "0.02"}),
    ),
}
RETIRED_ITEM = "Panna Cotta"  # deactivated half way through the period
RETIRED_CATEGORY = "Summer Specials"
PRICE_RISE = ("Margherita", "12.25")  # with a recipe change, so old sales must keep the old values
RECIPE_CHANGE = ("Margherita", "Mozzarella", "0.15")

VOID_RATE_PERCENT = 2
VOID_REASONS = ("Rang up the wrong item", "Customer changed their mind", "Entered twice", "Wrong table's order")
WASTE = (("Fresh basil", "0.5", "Wilted"), ("Milk", "1", "Past its date"), ("Romaine lettuce", "1", "Browned"), ("Mozzarella", "0.25", "Dropped"))
COUNTED_WEEKLY = ("Pizza flour", "Olive oil", "Coffee beans", "Dried pasta")
PAYMENT_WEIGHTS = (("card", 62), ("cash", 33), ("other", 5))


class SimulatedClock:
    def __init__(self) -> None:
        self.current = datetime.now(timezone.utc)

    def move_to(self, when: datetime) -> None:
        self.current = when

    def utc_now(self) -> str:
        """The same format as core.utc_now(), so stored rows look and sort the same."""
        return self.current.astimezone(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _services_use(clock: SimulatedClock) -> Iterator[None]:
    modules = [m for m in (audit, auth, inventory, menu, sales, settings, staff) if hasattr(m, "utc_now")]
    originals = [module.utc_now for module in modules]
    for module in modules:
        module.utc_now = clock.utc_now
    try:
        yield
    finally:
        for module, original in zip(modules, originals):
            module.utc_now = original


@dataclass
class Restaurant:
    conn: sqlite3.Connection
    rng: random.Random
    clock: SimulatedClock
    manager: Actor | None = None
    cashier: Actor | None = None
    clerk: Actor | None = None
    passwords: dict[str, str] = field(default_factory=dict)
    ingredients: dict[str, int] = field(default_factory=dict)
    pars: dict[str, int] = field(default_factory=dict)
    items: dict[str, int] = field(default_factory=dict)
    categories: dict[str, int] = field(default_factory=dict)


Event = tuple[datetime, Callable[[], object]]


def _local(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second).astimezone()


def _set_up(place: Restaurant) -> None:
    conn = place.conn
    place.passwords = {role: secrets.token_urlsafe(9) for role in ("manager", "cashier", "stock_clerk")}

    made = auth.create_first_manager(conn, "maria", "Maria Lopez", place.passwords["manager"])
    place.manager = Actor(made["id"], "maria", "Maria Lopez", Role.MANAGER)
    made = staff.create_staff(conn, place.manager, "carlos", "Carlos Diaz", "cashier", place.passwords["cashier"])
    place.cashier = Actor(made["id"], "carlos", "Carlos Diaz", Role.CASHIER)
    made = staff.create_staff(conn, place.manager, "sam", "Sam Okafor", "stock_clerk", place.passwords["stock_clerk"])
    place.clerk = Actor(made["id"], "sam", "Sam Okafor", Role.STOCK_CLERK)
    settings.update(conn, place.manager, restaurant_name=RESTAURANT_NAME, tax_rate_bp=TAX_RATE_BP)

    for name, unit, cost, low, par in INGREDIENTS:
        created = inventory.create_ingredient(
            conn, place.clerk, name, unit, parse_money(cost), parse_quantity(low)
        )
        place.ingredients[name] = created["id"]
        place.pars[name] = parse_quantity(par)
        inventory.receive_stock(conn, place.clerk, created["id"], place.pars[name], "Opening stock")

    for order, (category, items) in enumerate(MENU.items()):
        place.categories[category] = menu.create_category(conn, place.manager, category, order)["id"]
        for name, price, _weight, recipe in items:
            item = menu.create_item(conn, place.manager, place.categories[category], name, parse_money(price))
            place.items[name] = item["id"]
            if recipe:
                menu.set_recipe(
                    conn,
                    place.manager,
                    item["id"],
                    [
                        {"ingredient_id": place.ingredients[ingredient], "qty_milli": parse_quantity(qty)}
                        for ingredient, qty in recipe.items()
                    ],
                )


def _sellable_weights() -> dict[str, int]:
    return {name: weight for items in MENU.values() for name, _price, weight, _recipe in items}


def _pick_lines(rng: random.Random, names: list[str], weights: list[int]) -> dict[str, int]:
    lines: dict[str, int] = {}
    for name in rng.choices(names, weights, k=rng.choices((1, 2, 3, 4), (20, 40, 30, 10))[0]):
        lines[name] = lines.get(name, 0) + rng.choices((1, 2), (85, 15))[0]
    return lines


def _plan_day(place: Restaurant, day: date, day_number: int, days: int, today: date) -> list[Event]:
    """Everything that happens on one day, not yet in time order."""
    conn, rng, clerk = place.conn, place.rng, place.clerk
    events: list[Event] = []
    days_left = (today - day).days

    if day.weekday() in (0, 3):  # deliveries, Mondays and Thursdays
        late = {name for name, last in LATE_DELIVERIES.items() if days_left <= last}
        events.append((_local(day, 9, 30), partial(_deliver, place, late)))
    if day_number % 7 == 0:  # the manager's and the clerk's sign-ins, with one mistyped
        events.append((_local(day, 8, 55), partial(_sign_in, place, "maria", place.passwords["manager"])))
        events.append((_local(day, 9, 5), partial(_sign_in, place, "sam", "not-the-password")))
        events.append((_local(day, 9, 6), partial(_sign_in, place, "sam", place.passwords["stock_clerk"])))
    if day_number == days // 2:
        events.append((_local(day, 10), partial(_menu_changes, place)))
    if day_number % 3 == 0:
        wasted, qty, note = rng.choice(WASTE)
        action = partial(inventory.record_waste, conn, clerk, place.ingredients[wasted], parse_quantity(qty), note)
        events.append((_local(day, 22), action))
    if day.weekday() == 6:  # the Sunday count
        variances = {name: rng.choice((-200, -100, 0, 0, 50)) for name in COUNTED_WEEKLY}
        events.append((_local(day, 22, 30), partial(_count, place, variances)))
    if day_number % 10 == 5:
        oil = place.ingredients["Olive oil"]
        action = partial(inventory.adjust_stock, conn, clerk, oil, parse_quantity("-0.25"), "Bottle broke")
        events.append((_local(day, 16), action))

    available = _sellable_weights()
    if day_number >= days // 2:
        available.pop(RETIRED_ITEM)
        for name, _price, _weight, _recipe in MENU[RETIRED_CATEGORY]:
            available.pop(name)
    names, weights = list(available), list(available.values())
    methods, method_weights = zip(*PAYMENT_WEIGHTS)
    busy = {4: 12, 5: 16, 6: 8}.get(day.weekday(), 0)
    for _ in range(rng.randint(20, 28) + busy):
        hour = rng.randint(11, 13) if rng.random() < 0.45 else rng.randint(17, 21)
        when = _local(day, hour, rng.randint(0, 59), rng.randint(0, 59))
        sale = _PlannedSale(
            place,
            _pick_lines(rng, names, weights),
            rng.choices(methods, method_weights)[0],
            place.manager if rng.random() < 0.15 else place.cashier,
        )
        events.append((when, sale.record))
        minutes_until_void = rng.randint(1, 15)
        reason = rng.choice(VOID_REASONS)
        if rng.randrange(100) < VOID_RATE_PERCENT:
            events.append((when + timedelta(minutes=minutes_until_void), partial(sale.void, reason)))
    return events


@dataclass
class _PlannedSale:
    place: Restaurant
    lines: dict[str, int]
    method: str
    cashier: Actor
    sale_id: int | None = None

    def record(self) -> None:
        lines = [
            {"menu_item_id": self.place.items[name], "quantity": quantity}
            for name, quantity in self.lines.items()
        ]
        self.sale_id = sales.record_sale(self.place.conn, self.cashier, lines, self.method)["id"]

    def void(self, reason: str) -> None:
        sales.void_sale(self.place.conn, self.place.manager, self.sale_id, reason)


def _deliver(place: Restaurant, late: set[str]) -> None:
    for name, ingredient_id in place.ingredients.items():
        if name in late:
            continue
        on_hand = inventory.get_ingredient(place.conn, place.clerk, ingredient_id)["on_hand_milli"]
        needed = place.pars[name] - on_hand
        if needed > 0:
            whole_half_units = -(-needed // 500) * 500  # suppliers don't sell 3.137 kg
            inventory.receive_stock(place.conn, place.clerk, ingredient_id, whole_half_units, "Delivery")


def _count(place: Restaurant, variances: dict[str, int]) -> None:
    for name, variance in variances.items():
        ingredient_id = place.ingredients[name]
        on_hand = inventory.get_ingredient(place.conn, place.clerk, ingredient_id)["on_hand_milli"]
        inventory.record_count(place.conn, place.clerk, ingredient_id, max(on_hand + variance, 0), "Weekly count")


def _sign_in(place: Restaurant, username: str, password: str) -> None:
    try:
        auth.authenticate(place.conn, username, password)
    except AuthenticationError:
        pass  # a mistyped password belongs in the audit log, and there it is


def _menu_changes(place: Restaurant) -> None:
    conn, manager = place.conn, place.manager
    item, price = PRICE_RISE
    menu.update_item(conn, manager, place.items[item], price_cents=parse_money(price))
    name, changed, qty = RECIPE_CHANGE
    recipe = [
        {"ingredient_id": line["ingredient_id"], "qty_milli": line["qty_milli"]}
        for line in menu.get_recipe(conn, manager, place.items[name])
    ]
    for line in recipe:
        if line["ingredient_id"] == place.ingredients[changed]:
            line["qty_milli"] = parse_quantity(qty)
    menu.set_recipe(conn, manager, place.items[name], recipe)
    menu.set_item_active(conn, manager, place.items[RETIRED_ITEM], False)
    menu.set_category_active(conn, manager, place.categories[RETIRED_CATEGORY], False)


def seed(conn: sqlite3.Connection, days: int = DEFAULT_DAYS, now: datetime | None = None) -> Restaurant:
    """Fill an empty database, ending at ``now``. Nothing dated later than that is written."""
    now = (now or datetime.now(timezone.utc)).astimezone()
    today = now.date()
    start = today - timedelta(days=days)
    clock = SimulatedClock()
    place = Restaurant(conn, random.Random(SEED), clock)
    with _services_use(clock):
        clock.move_to(_local(start, 8))
        _set_up(place)
        events: list[Event] = []
        for number in range(1, days + 1):
            day = start + timedelta(days=number)
            events += _plan_day(place, day, number, days, today)
        for when, action in sorted(events, key=lambda event: event[0]):
            if when <= now:
                clock.move_to(when)
                action()
    return place


def check_consistency(conn: sqlite3.Connection) -> None:
    """Raise unless ledger and totals agree, the things backdating could have broken."""
    checks = {
        "on-hand differs from the ledger": (
            "SELECT i.id FROM ingredients i WHERE i.on_hand_milli <> "
            "(SELECT COALESCE(SUM(m.delta_milli), 0) FROM stock_movements m WHERE m.ingredient_id = i.id)"
        ),
        "a sale's subtotal differs from its lines": (
            "SELECT s.id FROM sales s WHERE s.subtotal_cents <> "
            "(SELECT COALESCE(SUM(l.line_total_cents), 0) FROM sale_items l WHERE l.sale_id = s.id)"
        ),
        "a sale's movements are dated differently from the sale": (
            "SELECT s.id FROM sales s JOIN stock_movements m ON m.sale_id = s.id "
            "WHERE m.movement_type = 'sold' AND m.created_at <> s.created_at"
        ),
        "a voided sale's stock was not returned exactly": (
            "SELECT m.sale_id FROM stock_movements m JOIN sales s ON s.id = m.sale_id "
            "WHERE s.voided_at IS NOT NULL GROUP BY m.sale_id, m.ingredient_id HAVING SUM(m.delta_milli) <> 0"
        ),
        "a sale that stands has a void reversal": (
            "SELECT m.sale_id FROM stock_movements m JOIN sales s ON s.id = m.sale_id "
            "WHERE s.voided_at IS NULL AND m.movement_type = 'void_reversal'"
        ),
    }
    for problem, query in checks.items():
        found = conn.execute(query).fetchall()
        if found:
            raise RuntimeError(f"Demo data is inconsistent: {problem} ({len(found)} found).")
    for row in conn.execute("SELECT id, subtotal_cents, tax_rate_bp, tax_cents, total_cents FROM sales"):
        if row["tax_cents"] != calculate_tax_cents(row["subtotal_cents"], row["tax_rate_bp"]):
            raise RuntimeError(f"Demo data is inconsistent: sale {row['id']} has the wrong tax.")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("Demo data is inconsistent: a foreign key is broken.")


def _print_report(place: Restaurant, path: Path, days: int) -> None:
    conn = place.conn
    live, voided, revenue = conn.execute(
        "SELECT COALESCE(SUM(voided_at IS NULL), 0), COALESCE(SUM(voided_at IS NOT NULL), 0), "
        "COALESCE(SUM(CASE WHEN voided_at IS NULL THEN total_cents END), 0) FROM sales"
    ).fetchone()
    low = inventory.list_low_stock(conn, place.clerk)
    print(f"Filled {path} with {days} days of demo data for {RESTAURANT_NAME}.")
    print(f"  {live} sales, {format_money(revenue)} with tax, and {voided} voided.")
    print(f"  {len(low)} ingredients low on stock: " + ", ".join(
        f"{row['name']} ({format_quantity(row['on_hand_milli'])} {row['unit']})" for row in low
    ))
    print("  Ledger and sale totals checked: consistent.")
    print()
    print("Demo accounts. Save these passwords now: they are shown only this once.")
    for role, actor in (("manager", place.manager), ("cashier", place.cashier), ("stock_clerk", place.clerk)):
        print(f"  {role:<12} username: {actor.username:<8} password: {place.passwords[role]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill an empty database with demo data.")
    parser.add_argument("database", type=Path, help="the database file, created if it doesn't exist")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="days of sales, ending today")
    args = parser.parse_args(argv)
    if not 1 <= args.days <= 366:
        parser.error("--days must be from 1 to 366")

    args.database.parent.mkdir(parents=True, exist_ok=True)
    try:
        opened = connect(args.database)
    except (sqlite3.Error, RuntimeError) as error:
        print(f"Refused: {args.database} can't be used as a database. ({error})", file=sys.stderr)
        return EXIT_REFUSED
    with closing(opened) as conn:
        if auth.has_accounts(conn):
            print(
                f"Refused: {args.database} already has staff accounts, so it was left as it is. "
                "Demo data only goes into a new, empty database.",
                file=sys.stderr,
            )
            return EXIT_REFUSED
        conn.execute("PRAGMA synchronous = OFF")  # this data can be remade, so favour speed
        place = seed(conn, args.days)
        check_consistency(conn)
        conn.execute("PRAGMA synchronous = FULL")
        _print_report(place, args.database, args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
