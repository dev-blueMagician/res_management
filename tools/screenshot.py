"""Save a PNG of any screen, using a throwaway database filled with demo data.

    .venv\\Scripts\\python.exe tools\\screenshot.py --view sales --role cashier --out sales.png

Runs on Qt's offscreen platform with the software renderer. The offscreen platform
ships no fonts, so QT_QPA_FONTDIR is pointed at the Windows fonts or text is blank.
See docs/BRIDGE_API.md for the options.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from restaurant.core import Actor, Role, parse_money, parse_quantity  # noqa: E402
from restaurant.services import auth, inventory, menu, sales, settings, staff  # noqa: E402

DEMO_PASSWORD = "demo-password"  # for the throwaway database only
USERNAMES = {"manager": "maria", "cashier": "carlos", "stock_clerk": "sam"}
PRE_SIGN_IN_VIEWS = ("first-run", "login")
SECTION_VIEWS = (
    "home",
    "sales",
    "menu",
    "inventory",
    "reports",
    "staff",
    "settings",
    "audit",
)


def load_demo_data(conn: sqlite3.Connection) -> dict[str, Actor]:
    """Fill an empty database through the services. Returns an Actor per role name."""
    record = auth.create_first_manager(conn, "maria", "Maria Lopez", DEMO_PASSWORD)
    manager = Actor(record["id"], record["username"], record["full_name"], Role.MANAGER)
    settings.update(conn, manager, restaurant_name="Olive & Thyme", tax_rate_bp=825)
    cashier_row = staff.create_staff(conn, manager, "carlos", "Carlos Diaz", "cashier", DEMO_PASSWORD)
    clerk_row = staff.create_staff(conn, manager, "sam", "Sam Okafor", "stock_clerk", DEMO_PASSWORD)
    cashier = Actor(cashier_row["id"], "carlos", "Carlos Diaz", Role.CASHIER)
    clerk = Actor(clerk_row["id"], "sam", "Sam Okafor", Role.STOCK_CLERK)

    ingredients = {}
    for name, unit, cost, low, received in (
        ("Flour", "kg", "1.20", "5", "40"),
        ("Tomatoes", "kg", "3.40", "4", "6"),
        ("Mozzarella", "kg", "9.80", "3", "2.5"),
        ("Basil", "bunch", "1.75", "2", "1"),
        ("Coffee beans", "kg", "14.50", "1", "8"),
        ("Milk", "L", "1.10", "6", "24"),
    ):
        created = inventory.create_ingredient(
            conn, clerk, name, unit, parse_money(cost), parse_quantity(low)
        )
        ingredients[name] = created["id"]
        inventory.receive_stock(conn, clerk, created["id"], parse_quantity(received), "Opening stock")

    categories = {}
    for order, name in enumerate(("Pizza", "Pasta", "Drinks", "Desserts")):
        categories[name] = menu.create_category(conn, manager, name, order)["id"]

    items = {}
    for category, name, price in (
        ("Pizza", "Margherita", "11.50"),
        ("Pizza", "Marinara", "9.50"),
        ("Pizza", "Quattro Formaggi", "13.75"),
        ("Pasta", "Penne Pomodoro", "10.25"),
        ("Pasta", "Tagliatelle Ragu", "12.90"),
        ("Drinks", "Espresso", "2.80"),
        ("Drinks", "Cappuccino", "3.60"),
        ("Drinks", "Sparkling Water", "2.50"),
        ("Desserts", "Tiramisu", "6.25"),
    ):
        items[name] = menu.create_item(conn, manager, categories[category], name, parse_money(price))["id"]

    for name, recipe in (
        ("Margherita", {"Flour": "0.25", "Tomatoes": "0.15", "Mozzarella": "0.125", "Basil": "0.1"}),
        ("Marinara", {"Flour": "0.25", "Tomatoes": "0.2"}),
        ("Penne Pomodoro", {"Flour": "0.12", "Tomatoes": "0.2"}),
        ("Espresso", {"Coffee beans": "0.018"}),
        ("Cappuccino", {"Coffee beans": "0.018", "Milk": "0.15"}),
    ):
        menu.set_recipe(
            conn,
            manager,
            items[name],
            [
                {"ingredient_id": ingredients[ingredient], "qty_milli": parse_quantity(qty)}
                for ingredient, qty in recipe.items()
            ],
        )
    retired = menu.create_item(conn, manager, categories["Desserts"], "Panna Cotta", parse_money("5.75"))
    menu.set_item_active(conn, manager, retired["id"], False)

    for lines, method in (
        ({"Margherita": 2, "Sparkling Water": 2}, "card"),
        ({"Cappuccino": 2, "Tiramisu": 1}, "cash"),
        ({"Marinara": 1, "Espresso": 1}, "card"),
        ({"Tagliatelle Ragu": 3, "Sparkling Water": 3}, "other"),
        ({"Margherita": 7}, "cash"),
    ):
        sales.record_sale(
            conn,
            cashier,
            [{"menu_item_id": items[name], "quantity": qty} for name, qty in lines.items()],
            method,
        )
    wrong = sales.record_sale(conn, cashier, [{"menu_item_id": items["Espresso"], "quantity": 4}], "cash")
    sales.void_sale(conn, manager, wrong["id"], "Entered the wrong item")
    inventory.record_waste(conn, clerk, ingredients["Milk"], parse_quantity("1.5"), "Past its date")
    inventory.record_count(conn, clerk, ingredients["Flour"], parse_quantity("38"), "Weekly count")
    return {"manager": manager, "cashier": cashier, "stock_clerk": clerk}


def _size(text: str) -> tuple[int, int]:
    try:
        width, height = text.lower().split("x")
        return int(width), int(height)
    except ValueError:
        raise argparse.ArgumentTypeError("use WIDTHxHEIGHT, such as 1280x800") from None


def main() -> int:
    parser = argparse.ArgumentParser(description="Save a PNG of a screen with demo data.")
    parser.add_argument("--view", default="home", choices=(*PRE_SIGN_IN_VIEWS, *SECTION_VIEWS))
    parser.add_argument("--role", default="manager", choices=tuple(USERNAMES))
    parser.add_argument("--size", default="1280x800", type=_size, help="WIDTHxHEIGHT")
    parser.add_argument("--out", default="screenshot.png", type=Path)
    parser.add_argument(
        "--js",
        default="",
        help="a JavaScript expression to run in Main.qml's root scope before the capture, "
        "such as \"changePasswordDialog.open()\"",
    )
    args = parser.parse_args()

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QUICK_BACKEND"] = "software"
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ["WINDIR"]) / "Fonts"))

    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlEngine, QQmlExpression
    from PySide6.QtQuickControls2 import QQuickStyle

    from restaurant.bridge.api import MAIN_QML, Api, create_engine, destroy_engine
    from restaurant.db.connection import connect

    app = QGuiApplication(sys.argv[:1])
    QQuickStyle.setStyle("Basic")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        db_path = Path(folder) / "demo.db"
        conn = connect(db_path)
        if args.view != "first-run":
            load_demo_data(conn)
        api = Api(conn, db_path, Path(folder) / "backups")
        engine = create_engine(api)
        problems: list[str] = []
        engine.warnings.connect(lambda errors: problems.extend(e.toString() for e in errors))

        if args.view in SECTION_VIEWS:
            result = api.session.signIn(USERNAMES[args.role], DEMO_PASSWORD)
            if not result["ok"]:
                raise SystemExit(f"Sign-in failed: {result['error']}")
            result = api.session.goTo(args.view)
            if not result["ok"]:
                raise SystemExit(f"{args.role} can't open {args.view}: {result['error']}")

        engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
        if not engine.rootObjects():
            raise SystemExit("Main.qml failed to load:\n" + "\n".join(problems))
        window = engine.rootObjects()[0]
        window.setMinimumWidth(0)
        window.setMinimumHeight(0)
        window.resize(*args.size)

        if args.js:
            expression = QQmlExpression(QQmlEngine.contextForObject(window), window, args.js)
            expression.evaluate()
            if expression.hasError():
                problems.append(expression.error().toString())

        def capture() -> None:
            image = window.grabWindow()
            args.out.parent.mkdir(parents=True, exist_ok=True)
            saved = image.save(str(args.out))
            print(f"{args.out}: {image.width()}x{image.height()}" if saved else "Saving failed")
            app.exit(0 if saved else 1)

        QTimer.singleShot(500, capture)
        code = app.exec()
        for problem in problems:
            print(f"QML warning: {problem}", file=sys.stderr)
        destroy_engine(engine)
        api.shutdown()
    return 1 if problems else code


if __name__ == "__main__":
    sys.exit(main())
