-- Restaurant Management System: initial schema.
-- Money is stored as whole cents. Stock quantities are stored as whole
-- thousandths of a unit ("milli"). Timestamps are UTC ISO-8601 text.

CREATE TABLE staff (
    id              INTEGER PRIMARY KEY,
    username        TEXT    NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(username)) > 0),
    full_name       TEXT    NOT NULL CHECK (length(trim(full_name)) > 0),
    password_hash   TEXT    NOT NULL,
    role            TEXT    NOT NULL CHECK (role IN ('manager', 'cashier', 'stock_clerk')),
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at      TEXT    NOT NULL
);

CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY,
    staff_id    INTEGER REFERENCES staff (id),  -- NULL for failed sign-ins by unknown users
    action      TEXT    NOT NULL,
    details     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);
CREATE INDEX idx_audit_log_created_at ON audit_log (created_at);

CREATE TABLE settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
INSERT INTO settings (key, value) VALUES
    ('restaurant_name', 'My Restaurant'),
    ('currency_symbol', '$'),
    ('tax_rate_bp', '0');  -- basis points: 825 means 8.25%

CREATE TABLE menu_categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE menu_items (
    id           INTEGER PRIMARY KEY,
    category_id  INTEGER NOT NULL REFERENCES menu_categories (id),
    name         TEXT    NOT NULL COLLATE NOCASE CHECK (length(trim(name)) > 0),
    price_cents  INTEGER NOT NULL CHECK (price_cents >= 0),
    active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    UNIQUE (category_id, name)
);

CREATE TABLE ingredients (
    id               INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0),
    unit             TEXT    NOT NULL CHECK (length(trim(unit)) > 0),
    unit_cost_cents  INTEGER NOT NULL DEFAULT 0 CHECK (unit_cost_cents >= 0),
    -- Maintained only by trg_stock_movements_apply. May go negative (FR-I5).
    on_hand_milli    INTEGER NOT NULL DEFAULT 0,
    low_milli        INTEGER NOT NULL DEFAULT 0 CHECK (low_milli >= 0),
    active           INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE recipe_items (
    menu_item_id   INTEGER NOT NULL REFERENCES menu_items (id) ON DELETE CASCADE,
    ingredient_id  INTEGER NOT NULL REFERENCES ingredients (id),
    qty_milli      INTEGER NOT NULL CHECK (qty_milli > 0),
    PRIMARY KEY (menu_item_id, ingredient_id)
);

CREATE TABLE sales (
    id              INTEGER PRIMARY KEY,
    staff_id        INTEGER NOT NULL REFERENCES staff (id),
    payment_method  TEXT    NOT NULL CHECK (payment_method IN ('cash', 'card', 'other')),
    subtotal_cents  INTEGER NOT NULL CHECK (subtotal_cents >= 0),
    tax_rate_bp     INTEGER NOT NULL CHECK (tax_rate_bp >= 0),
    tax_cents       INTEGER NOT NULL CHECK (tax_cents >= 0),
    total_cents     INTEGER NOT NULL CHECK (total_cents >= 0),
    created_at      TEXT    NOT NULL,
    voided_at       TEXT,
    voided_by       INTEGER REFERENCES staff (id),
    void_reason     TEXT,
    CHECK (total_cents = subtotal_cents + tax_cents),
    CHECK (
        (voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL)
        OR (voided_at IS NOT NULL AND voided_by IS NOT NULL
            AND void_reason IS NOT NULL AND length(trim(void_reason)) > 0)
    )
);
CREATE INDEX idx_sales_created_at ON sales (created_at);

CREATE TABLE sale_items (
    id                INTEGER PRIMARY KEY,
    sale_id           INTEGER NOT NULL REFERENCES sales (id),
    menu_item_id      INTEGER NOT NULL REFERENCES menu_items (id),
    -- Copied at sale time so later menu changes never alter past sales (FR-P4).
    name_as_sold      TEXT    NOT NULL,
    category_as_sold  TEXT    NOT NULL,
    unit_price_cents  INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    line_total_cents  INTEGER NOT NULL CHECK (line_total_cents >= 0),
    CHECK (line_total_cents = unit_price_cents * quantity)
);
CREATE INDEX idx_sale_items_sale_id ON sale_items (sale_id);

CREATE TABLE stock_movements (
    id             INTEGER PRIMARY KEY,
    ingredient_id  INTEGER NOT NULL REFERENCES ingredients (id),
    movement_type  TEXT    NOT NULL CHECK (movement_type IN
                       ('received', 'adjusted', 'wasted', 'sold', 'void_reversal')),
    delta_milli    INTEGER NOT NULL CHECK (delta_milli <> 0),
    staff_id       INTEGER NOT NULL REFERENCES staff (id),
    sale_id        INTEGER REFERENCES sales (id),
    note           TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL,
    -- Direction and sale link must match the movement type.
    CHECK (movement_type <> 'received' OR delta_milli > 0),
    CHECK (movement_type <> 'wasted' OR delta_milli < 0),
    CHECK (movement_type <> 'sold' OR (delta_milli < 0 AND sale_id IS NOT NULL)),
    CHECK (movement_type <> 'void_reversal' OR (delta_milli > 0 AND sale_id IS NOT NULL)),
    CHECK (movement_type IN ('sold', 'void_reversal') OR sale_id IS NULL)
);
CREATE INDEX idx_stock_movements_ingredient ON stock_movements (ingredient_id, created_at);
CREATE INDEX idx_stock_movements_sale ON stock_movements (sale_id);

-- The ledger is the source of truth: on-hand follows from it, and it is insert-only.
CREATE TRIGGER trg_stock_movements_apply
AFTER INSERT ON stock_movements
BEGIN
    UPDATE ingredients
       SET on_hand_milli = on_hand_milli + NEW.delta_milli
     WHERE id = NEW.ingredient_id;
END;

CREATE TRIGGER trg_stock_movements_no_update
BEFORE UPDATE ON stock_movements
BEGIN
    SELECT RAISE(ABORT, 'stock_movements is insert-only');
END;

CREATE TRIGGER trg_stock_movements_no_delete
BEFORE DELETE ON stock_movements
BEGIN
    SELECT RAISE(ABORT, 'stock_movements is insert-only');
END;
