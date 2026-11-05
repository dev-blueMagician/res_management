-- Money and quantity columns hold whole numbers only (NFR-3). A column's INTEGER type
-- alone lets floats and text through, and CHECK (x >= 0) is true for text, so these
-- triggers refuse anything else. They run after the column's type conversion, so '12'
-- and 12.0 still become the integer 12. Existing rows are not touched.

CREATE TRIGGER trg_menu_items_whole_numbers_insert
AFTER INSERT ON menu_items
WHEN typeof(NEW.price_cents) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'menu_items: price_cents must be a whole number');
END;

CREATE TRIGGER trg_menu_items_whole_numbers_update
AFTER UPDATE OF price_cents ON menu_items
WHEN typeof(NEW.price_cents) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'menu_items: price_cents must be a whole number');
END;

CREATE TRIGGER trg_ingredients_whole_numbers_insert
AFTER INSERT ON ingredients
WHEN typeof(NEW.unit_cost_cents) <> 'integer'
  OR typeof(NEW.on_hand_milli) <> 'integer'
  OR typeof(NEW.low_milli) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'ingredients: costs and quantities must be whole numbers');
END;

CREATE TRIGGER trg_ingredients_whole_numbers_update
AFTER UPDATE OF unit_cost_cents, on_hand_milli, low_milli ON ingredients
WHEN typeof(NEW.unit_cost_cents) <> 'integer'
  OR typeof(NEW.on_hand_milli) <> 'integer'
  OR typeof(NEW.low_milli) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'ingredients: costs and quantities must be whole numbers');
END;

CREATE TRIGGER trg_recipe_items_whole_numbers_insert
AFTER INSERT ON recipe_items
WHEN typeof(NEW.qty_milli) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'recipe_items: qty_milli must be a whole number');
END;

CREATE TRIGGER trg_recipe_items_whole_numbers_update
AFTER UPDATE OF qty_milli ON recipe_items
WHEN typeof(NEW.qty_milli) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'recipe_items: qty_milli must be a whole number');
END;

CREATE TRIGGER trg_sales_whole_numbers_insert
AFTER INSERT ON sales
WHEN typeof(NEW.subtotal_cents) <> 'integer'
  OR typeof(NEW.tax_rate_bp) <> 'integer'
  OR typeof(NEW.tax_cents) <> 'integer'
  OR typeof(NEW.total_cents) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'sales: amounts must be whole numbers');
END;

CREATE TRIGGER trg_sales_whole_numbers_update
AFTER UPDATE OF subtotal_cents, tax_rate_bp, tax_cents, total_cents ON sales
WHEN typeof(NEW.subtotal_cents) <> 'integer'
  OR typeof(NEW.tax_rate_bp) <> 'integer'
  OR typeof(NEW.tax_cents) <> 'integer'
  OR typeof(NEW.total_cents) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'sales: amounts must be whole numbers');
END;

CREATE TRIGGER trg_sale_items_whole_numbers_insert
AFTER INSERT ON sale_items
WHEN typeof(NEW.unit_price_cents) <> 'integer'
  OR typeof(NEW.quantity) <> 'integer'
  OR typeof(NEW.line_total_cents) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'sale_items: amounts must be whole numbers');
END;

CREATE TRIGGER trg_sale_items_whole_numbers_update
AFTER UPDATE OF unit_price_cents, quantity, line_total_cents ON sale_items
WHEN typeof(NEW.unit_price_cents) <> 'integer'
  OR typeof(NEW.quantity) <> 'integer'
  OR typeof(NEW.line_total_cents) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'sale_items: amounts must be whole numbers');
END;

-- Updates to this table are already refused by trg_stock_movements_no_update.
CREATE TRIGGER trg_stock_movements_whole_numbers_insert
AFTER INSERT ON stock_movements
WHEN typeof(NEW.delta_milli) <> 'integer'
BEGIN
    SELECT RAISE(ABORT, 'stock_movements: delta_milli must be a whole number');
END;
