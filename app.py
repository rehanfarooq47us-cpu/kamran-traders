import os
import sqlite3
from datetime import datetime

from flask import Flask, abort, flash, g, has_app_context, jsonify, redirect, render_template, request, session, url_for

app = Flask(__name__)


@app.after_request
def add_viewport_header(response):
    response.headers["X-View-Mode"] = "desktop"
    return response
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")
DATABASE = os.path.join(app.root_path, "inventory.db")

# Conversion constants: business rules
# Number of kilograms in one purchase unit of flour
KG_PER_PURCHASE_UNIT = 15
# Number of flatbreads produced from one kilogram of flour
FLATBREADS_PER_KG = 20
# Derived: number of flatbreads produced from one purchase unit
FLATBREADS_PER_PURCHASE_UNIT = KG_PER_PURCHASE_UNIT * FLATBREADS_PER_KG


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_column_exists(table_name, column_name, column_def):
    db = get_db()
    columns = [row[1] for row in db.execute(f"PRAGMA table_info({table_name})")]
    if column_name not in columns:
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")


def init_db():
    if not has_app_context():
        with app.app_context():
            return init_db()

    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT UNIQUE NOT NULL,
            quantity_on_hand INTEGER NOT NULL DEFAULT 0,
            last_purchase_price REAL NOT NULL DEFAULT 0,
            last_sale_price REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_type TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            total REAL NOT NULL,
            created_at TEXT NOT NULL,
            customer_name TEXT,
            supplier_name TEXT,
            bill_number TEXT,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            address TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            address TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            expense_date TEXT NOT NULL,
            category TEXT,
            note TEXT,
            entry_type TEXT NOT NULL DEFAULT 'expense',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ledger_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            party_type TEXT NOT NULL,
            party_name TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            payment_date TEXT NOT NULL,
            bill_number TEXT,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    ensure_column_exists("transactions", "customer_name", "customer_name TEXT")
    ensure_column_exists("transactions", "supplier_name", "supplier_name TEXT")
    ensure_column_exists("transactions", "bill_number", "bill_number TEXT")
    ensure_column_exists("transactions", "note", "note TEXT")
    ensure_column_exists("expenses", "entry_type", "entry_type TEXT NOT NULL DEFAULT 'expense'")
    db.commit()


def normalize_item_name(item_name):
    name = (item_name or "").strip()
    lower_name = name.lower()
    if lower_name == "flour":
        return "Flour"
    if lower_name in ["flatbread", "flat bread", "roti", "paratha"]:
        return "Flatbread"
    return name


def get_inventory_delta_for_purchase(item_name, quantity):
    normalized = normalize_item_name(item_name).lower()
    if normalized == "flour":
        return int(quantity) * KG_PER_PURCHASE_UNIT
    return int(quantity)


def get_inventory_item(db, item_name):
    normalized_name = normalize_item_name(item_name)
    normalized_lower = normalized_name.lower()
    rows = db.execute(
        "SELECT id, item_name, quantity_on_hand, last_purchase_price FROM inventory WHERE LOWER(item_name) = ? ORDER BY item_name",
        (normalized_lower,),
    ).fetchall()
    if not rows:
        return None

    canonical_row = next((row for row in rows if row["item_name"] == normalized_name), rows[0])
    total_qty = sum(float(row["quantity_on_hand"] or 0) for row in rows)
    last_price = max(float(row["last_purchase_price"] or 0) for row in rows)

    if len(rows) > 1 or canonical_row["item_name"] != normalized_name:
        db.execute(
            "UPDATE inventory SET item_name = ?, quantity_on_hand = ?, last_purchase_price = ? WHERE id = ?",
            (normalized_name, total_qty, last_price, canonical_row["id"]),
        )
        for row in rows:
            if row["id"] != canonical_row["id"]:
                db.execute("DELETE FROM inventory WHERE id = ?", (row["id"],))
        db.commit()
        return db.execute(
            "SELECT id, item_name, quantity_on_hand, last_purchase_price FROM inventory WHERE id = ?",
            (canonical_row["id"],),
        ).fetchone()

    return canonical_row


def get_summary():
    db = get_db()
    purchases = db.execute(
        "SELECT COALESCE(SUM(total), 0) AS total FROM transactions WHERE transaction_type = 'purchase'"
    ).fetchone()
    sales = db.execute(
        "SELECT COALESCE(SUM(total), 0) AS total FROM transactions WHERE transaction_type = 'sale'"
    ).fetchone()
    inventory_rows = db.execute(
        "SELECT item_name, quantity_on_hand, last_purchase_price FROM inventory ORDER BY item_name"
    ).fetchall()
    expenses = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses"
    ).fetchone()
    top_items = db.execute(
        "SELECT item_name, SUM(quantity) AS total_qty FROM transactions WHERE transaction_type = 'sale' GROUP BY item_name ORDER BY total_qty DESC LIMIT 5"
    ).fetchall()

    total_purchases = float(purchases["total"] or 0)
    total_sales = float(sales["total"] or 0)
    total_expenses = float(expenses["total"] or 0)
    stock_units = sum(int(row["quantity_on_hand"]) for row in inventory_rows)
    inventory_items = len(inventory_rows)
    low_stock_items = sum(1 for row in inventory_rows if int(row["quantity_on_hand"]) <= 3)
    inventory_value = round(
        sum(int(row["quantity_on_hand"]) * float(row["last_purchase_price"] or 0) for row in inventory_rows),
        2,
    )

    return {
        "total_purchases": total_purchases,
        "total_sales": total_sales,
        "total_expenses": round(total_expenses, 2),
        "profit": round(total_sales - total_purchases - total_expenses, 2),
        "inventory_items": inventory_items,
        "stock_units": stock_units,
        "low_stock_items": low_stock_items,
        "inventory_value": inventory_value,
        "top_items": [
            {"item_name": row["item_name"], "total_qty": int(row["total_qty"])} for row in top_items
        ],
    }


def get_business_metrics(db, reference_date=None):
    reference_date = reference_date or datetime.utcnow()
    month_key = reference_date.strftime("%Y-%m")
    day_key = reference_date.strftime("%Y-%m-%d")
    rows = db.execute(
        "SELECT transaction_type, total, created_at FROM transactions"
    ).fetchall()

    monthly_sales = 0.0
    monthly_purchases = 0.0
    monthly_expenses = 0.0
    monthly_credit = 0.0
    monthly_payments = 0.0
    daily_sales = 0.0
    daily_purchases = 0.0
    daily_expenses = 0.0
    daily_credit = 0.0
    daily_payments = 0.0
    total_sales = 0.0
    total_purchases = 0.0
    total_expenses = 0.0
    total_credit = 0.0
    total_payments = 0.0

    for row in rows:
        created_at = row["created_at"] or ""
        total = float(row["total"] or 0)
        transaction_type = row["transaction_type"]
        if transaction_type == "sale":
            total_sales += total
            if created_at.startswith(month_key):
                monthly_sales += total
            if created_at.startswith(day_key):
                daily_sales += total
        elif transaction_type == "purchase":
            total_purchases += total
            if created_at.startswith(month_key):
                monthly_purchases += total
            if created_at.startswith(day_key):
                daily_purchases += total

    expense_rows = db.execute(
        "SELECT amount, expense_date FROM expenses"
    ).fetchall()
    for row in expense_rows:
        expense_date = row["expense_date"] or ""
        amount = float(row["amount"] or 0)
        total_expenses += amount
        if expense_date.startswith(month_key):
            monthly_expenses += amount
        if expense_date.startswith(day_key):
            daily_expenses += amount

    credit_rows = db.execute(
        "SELECT total, created_at, customer_name FROM transactions WHERE transaction_type = 'sale' AND customer_name IS NOT NULL AND customer_name != ''"
    ).fetchall()
    for row in credit_rows:
        created_at = row["created_at"] or ""
        amount = float(row["total"] or 0)
        total_credit += amount
        if created_at.startswith(month_key):
            monthly_credit += amount
        if created_at.startswith(day_key):
            daily_credit += amount

    payment_rows = db.execute(
        "SELECT amount, payment_date FROM ledger_payments WHERE party_type = 'customer'"
    ).fetchall()
    for row in payment_rows:
        payment_date = row["payment_date"] or ""
        amount = float(row["amount"] or 0)
        total_payments += amount
        if payment_date.startswith(month_key):
            monthly_payments += amount
        if payment_date.startswith(day_key):
            daily_payments += amount

    return {
        "monthly_sales": round(monthly_sales, 2),
        "monthly_purchases": round(monthly_purchases, 2),
        "monthly_expenses": round(monthly_expenses, 2),
        "monthly_credit": round(monthly_credit - monthly_payments, 2),
        "monthly_payments": round(monthly_payments, 2),
        "monthly_profit": round(monthly_sales - monthly_purchases - monthly_expenses, 2),
        "daily_sales": round(daily_sales, 2),
        "daily_purchases": round(daily_purchases, 2),
        "daily_expenses": round(daily_expenses, 2),
        "daily_credit": round(daily_credit - daily_payments, 2),
        "daily_payments": round(daily_payments, 2),
        "daily_profit": round(daily_sales - daily_purchases - daily_expenses, 2),
        "total_sales": round(total_sales, 2),
        "total_purchases": round(total_purchases, 2),
        "total_expenses": round(total_expenses, 2),
        "total_credit": round(total_credit - total_payments, 2),
        "total_payments": round(total_payments, 2),
        "total_profit": round(total_sales - total_purchases - total_expenses, 2),
    }


def get_latest_flour_purchase_price(db):
    row = db.execute(
        "SELECT unit_price FROM transactions WHERE transaction_type = 'purchase' AND LOWER(item_name) = 'flour' ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    return float(row["unit_price"] or 0) if row else 0.0


def get_flatbread_rate(db):
    flour_price = get_latest_flour_purchase_price(db)
    # Use derived constant for flatbreads per purchase unit
    return flour_price / FLATBREADS_PER_PURCHASE_UNIT if flour_price > 0 else 0.0


@app.route("/")
def index():
    init_db()
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if username == "admin" and password == "admin123":
        session["role"] = "admin"
        flash("Welcome Admin", "success")
        return redirect(url_for("purchases_page"))

    if username == "employee" and password == "employee123":
        session["role"] = "employee"
        flash("Welcome Employee", "success")
        return redirect(url_for("purchases_page"))

    flash("Invalid credentials", "error")
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.pop("role", None)
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard_page():
    init_db()
    db = get_db()
    summary = get_summary()
    metrics = get_business_metrics(db)
    transactions = db.execute(
        "SELECT id, transaction_type, item_name, quantity, unit_price, total, created_at FROM transactions ORDER BY id DESC LIMIT 10"
    ).fetchall()
    return render_template(
        "dashboard.html",
        summary=summary,
        metrics=metrics,
        transactions=transactions,
    )


@app.route("/sales")
def sales_page():
    init_db()
    db = get_db()
    transactions = db.execute(
        "SELECT id, item_name, quantity, unit_price, total, customer_name, created_at FROM transactions WHERE transaction_type = 'sale' ORDER BY id DESC"
    ).fetchall()
    customers = db.execute(
        "SELECT id, name FROM customers ORDER BY name"
    ).fetchall()
    flatbread_rate = get_flatbread_rate(db)
    flatbread_item = get_inventory_item(db, "Flatbread")
    flatbread_available = float(flatbread_item["quantity_on_hand"] or 0) if flatbread_item else 0.0
    flatbread_value = round(flatbread_available * flatbread_rate, 2)
    current_date = datetime.utcnow().strftime("%Y-%m-%d")
    total_sales_amount = sum(float(row["total"] or 0) for row in transactions)
    total_sales_quantity = sum(int(row["quantity"] or 0) for row in transactions)
    return render_template(
        "sales.html",
        transactions=transactions,
        customers=customers,
        flatbread_rate=flatbread_rate,
        flatbread_available=flatbread_available,
        flatbread_value=flatbread_value,
        total_sales_amount=total_sales_amount,
        total_sales_quantity=total_sales_quantity,
        current_date=current_date,
    )


@app.route("/sales/<int:transaction_id>/edit", methods=["GET", "POST"])
def edit_sale(transaction_id):
    init_db()
    db = get_db()
    transaction = db.execute(
        "SELECT id, item_name, quantity, unit_price, total, created_at, customer_name FROM transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    if not transaction:
        flash("Sale not found", "error")
        return redirect(url_for("sales_page"))

    if request.method == "POST":
        sale_date = request.form.get("sale_date", "").strip()
        customer_name = request.form.get("customer_name", "").strip()
        item_name = normalize_item_name(request.form.get("item_name", "").strip())
        quantity = int(request.form.get("quantity", 0))
        unit_price = float(request.form.get("unit_price", 0))
        total_amount = request.form.get("total_amount", "").strip()

        if not customer_name or not item_name or quantity <= 0:
            flash("Customer, item, and quantity are required", "error")
            return redirect(url_for("edit_sale", transaction_id=transaction_id))

        total = float(total_amount) if total_amount else quantity * unit_price
        db.execute(
            "UPDATE transactions SET item_name = ?, quantity = ?, unit_price = ?, total = ?, created_at = ?, customer_name = ? WHERE id = ?",
            (item_name, quantity, unit_price, total, sale_date or transaction["created_at"], customer_name, transaction_id),
        )
        db.commit()
        flash("Sale updated", "success")
        return redirect(url_for("sales_page"))

    return jsonify({
        "id": transaction["id"],
        "item_name": transaction["item_name"],
        "quantity": transaction["quantity"],
        "unit_price": transaction["unit_price"],
        "total": transaction["total"],
        "created_at": transaction["created_at"],
        "customer_name": transaction["customer_name"],
    })


@app.route("/sales/<int:transaction_id>/delete", methods=["POST", "DELETE"])
def delete_sale(transaction_id):
    init_db()
    db = get_db()
    transaction = db.execute("SELECT id FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if not transaction:
        flash("Sale not found", "error")
        return redirect(url_for("sales_page"))
    db.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    db.commit()
    flash("Sale deleted", "success")
    return redirect(url_for("sales_page"))


@app.route("/sales", methods=["POST"])
def add_sale():
    init_db()
    sale_date = request.form.get("sale_date", "").strip()
    customer_name = request.form.get("customer_name", "").strip()
    item_name = normalize_item_name(request.form.get("item_name", "").strip())
    quantity = int(request.form.get("quantity", 0))
    unit_price = float(request.form.get("unit_price", 0))
    total_amount = request.form.get("total_amount", "").strip()
    action = (request.form.get("action") or "save").strip().lower()

    if not customer_name or not item_name or quantity <= 0:
        flash("Customer, item, and quantity are required", "error")
        return redirect(url_for("sales_page"))

    if unit_price <= 0 and total_amount:
        total_value = float(total_amount)
        if total_value < 0:
            flash("Total amount must be valid", "error")
            return redirect(url_for("sales_page"))
        unit_price = total_value / quantity if quantity > 0 else 0

    if unit_price <= 0:
        flash("Unit price or total amount is required", "error")
        return redirect(url_for("sales_page"))

    db = get_db()
    if normalize_item_name(item_name).lower() == "flatbread":
        inventory_item = get_inventory_item(db, "Flatbread")
    else:
        inventory_item = get_inventory_item(db, item_name)

    if not inventory_item or float(inventory_item["quantity_on_hand"] or 0) < quantity:
        flash("Not enough stock available for this sale", "error")
        return redirect(url_for("sales_page"))

    timestamp = sale_date or datetime.utcnow().strftime("%Y-%m-%d")
    total = float(total_amount) if total_amount else quantity * unit_price
    if total <= 0:
        total = quantity * unit_price
    cursor = db.execute(
        "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, customer_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sale", item_name, quantity, unit_price, total, timestamp, customer_name),
    )
    db.execute(
        "UPDATE inventory SET quantity_on_hand = quantity_on_hand - ?, last_sale_price = ? WHERE id = ?",
        (quantity, unit_price, inventory_item["id"]),
    )
    db.commit()

    receipt_id = cursor.lastrowid
    flash(f"Sale recorded for {quantity} {item_name}", "success")
    if action == "save_print":
        return redirect(url_for("sale_receipt_page", receipt_id=receipt_id))
    return redirect(url_for("sales_page"))


@app.route("/sales/receipt/<int:receipt_id>")
def sale_receipt_page(receipt_id):
    init_db()
    db = get_db()
    sale = db.execute(
        "SELECT id, item_name, quantity, unit_price, total, created_at, customer_name FROM transactions WHERE transaction_type = 'sale' AND id = ?",
        (receipt_id,),
    ).fetchone()
    if not sale:
        flash("Receipt not found", "error")
        return redirect(url_for("sales_page"))
    return render_template("sale_receipt.html", sale=sale)


@app.route("/customers")
def customers_page():
    init_db()
    db = get_db()
    customers = db.execute(
        "SELECT id, name, phone, address FROM customers ORDER BY name"
    ).fetchall()
    metrics = get_business_metrics(db)
    return render_template("customers.html", customers=customers, metrics=metrics)


@app.route("/customers", methods=["POST"])
def add_customer():
    init_db()
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    if not name:
        flash("Customer name is required", "error")
        return redirect(url_for("customers_page"))

    db = get_db()
    db.execute(
        "INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)",
        (name, phone or None, address or None),
    )
    db.commit()
    flash("Customer saved successfully", "success")
    return redirect(url_for("customers_page"))


@app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def edit_customer(customer_id):
    init_db()
    db = get_db()
    customer = db.execute(
        "SELECT id, name, phone, address FROM customers WHERE id = ?",
        (customer_id,),
    ).fetchone()
    if not customer:
        flash("Customer not found", "error")
        return redirect(url_for("customers_page"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        if not name:
            flash("Customer name is required", "error")
            return redirect(url_for("edit_customer", customer_id=customer_id))
        db.execute(
            "UPDATE customers SET name = ?, phone = ?, address = ? WHERE id = ?",
            (name, phone or None, address or None, customer_id),
        )
        db.commit()
        flash("Customer updated successfully", "success")
        return redirect(url_for("customers_page"))

    return render_template("edit_customer.html", customer=customer)


@app.route("/customers/<int:customer_id>/delete", methods=["POST"])
def delete_customer(customer_id):
    init_db()
    db = get_db()
    db.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    db.commit()
    flash("Customer deleted", "success")
    return redirect(url_for("customers_page"))


@app.route("/customers/<int:customer_id>")
def customer_detail_page(customer_id):
    init_db()
    db = get_db()
    customer = db.execute(
        "SELECT id, name, phone, email, address FROM customers WHERE id = ?",
        (customer_id,),
    ).fetchone()
    if not customer:
        flash("Customer not found", "error")
        return redirect(url_for("customers_page"))

    sales = db.execute(
        "SELECT created_at, item_name, quantity, unit_price, total FROM transactions WHERE transaction_type = 'sale' AND customer_name = ? ORDER BY id DESC",
        (customer["name"],),
    ).fetchall()
    return render_template(
        "customer_detail.html",
        customer=customer,
        sales=sales,
        invoices=[],
        payments=[],
    )


@app.route("/suppliers")
def suppliers_page():
    init_db()
    db = get_db()
    suppliers = db.execute(
        "SELECT id, name, phone, address FROM suppliers ORDER BY name"
    ).fetchall()
    return render_template("suppliers.html", suppliers=suppliers)


@app.route("/suppliers", methods=["POST"])
def add_supplier():
    init_db()
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    if not name:
        flash("Supplier name is required", "error")
        return redirect(url_for("suppliers_page"))

    db = get_db()
    db.execute(
        "INSERT INTO suppliers (name, phone, address) VALUES (?, ?, ?)",
        (name, phone or None, address or None),
    )
    db.commit()
    flash("Supplier saved successfully", "success")
    return redirect(url_for("suppliers_page"))


@app.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
def edit_supplier(supplier_id):
    init_db()
    db = get_db()
    supplier = db.execute(
        "SELECT id, name, phone, address FROM suppliers WHERE id = ?",
        (supplier_id,),
    ).fetchone()
    if not supplier:
        flash("Supplier not found", "error")
        return redirect(url_for("suppliers_page"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        if not name:
            flash("Supplier name is required", "error")
            return redirect(url_for("edit_supplier", supplier_id=supplier_id))
        db.execute(
            "UPDATE suppliers SET name = ?, phone = ?, address = ? WHERE id = ?",
            (name, phone or None, address or None, supplier_id),
        )
        db.commit()
        flash("Supplier updated successfully", "success")
        return redirect(url_for("suppliers_page"))

    return render_template("edit_supplier.html", supplier=supplier)


@app.route("/suppliers/<int:supplier_id>/delete", methods=["POST"])
def delete_supplier(supplier_id):
    init_db()
    db = get_db()
    db.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
    db.commit()
    flash("Supplier deleted", "success")
    return redirect(url_for("suppliers_page"))


@app.route("/suppliers/<int:supplier_id>")
def supplier_detail_page(supplier_id):
    init_db()
    db = get_db()
    supplier = db.execute(
        "SELECT id, name, phone, email, address FROM suppliers WHERE id = ?",
        (supplier_id,),
    ).fetchone()
    if not supplier:
        flash("Supplier not found", "error")
        return redirect(url_for("suppliers_page"))

    purchases = db.execute(
        "SELECT created_at, item_name, quantity, unit_price, total FROM transactions WHERE transaction_type = 'purchase' AND supplier_name = ? ORDER BY id DESC",
        (supplier["name"],),
    ).fetchall()
    return render_template(
        "supplier_detail.html",
        supplier=supplier,
        purchases=purchases,
    )


@app.route("/ledger/payment", methods=["POST"])
def add_ledger_payment():
    init_db()
    db = get_db()
    party_type = (request.form.get("party_type") or "customer").strip().lower()
    party_name = request.form.get("party_name", "").strip()
    payment_date = request.form.get("payment_date", "").strip()
    amount = float(request.form.get("amount", 0))
    note = request.form.get("note", "").strip()
    if not party_name or not payment_date or amount <= 0:
        flash("Party name, date, and amount are required", "error")
        return redirect(url_for("ledger_page"))

    db.execute(
        "INSERT INTO ledger_payments (party_type, party_name, amount, payment_date, note) VALUES (?, ?, ?, ?, ?)",
        (party_type, party_name, amount, payment_date, note or None),
    )
    db.commit()
    flash("Payment recorded", "success")
    return redirect(url_for("ledger_page", view="customer" if party_type == "customer" else "supplier"))


@app.route("/ledger/pay-bill", methods=["POST"])
def pay_bill():
    init_db()
    db = get_db()
    party_type = (request.form.get("party_type") or "supplier").strip().lower()
    party_name = request.form.get("party_name", "").strip()
    payment_date = request.form.get("payment_date", "").strip()
    bill_number = request.form.get("bill_number", "").strip()
    amount = float(request.form.get("amount", 0))
    note = request.form.get("note", "").strip()
    if not party_name or not payment_date or not bill_number or amount <= 0:
        flash("Party name, date, bill number, and amount are required", "error")
        return redirect(url_for("ledger_page", view="supplier"))

    db.execute(
        "INSERT INTO ledger_payments (party_type, party_name, amount, payment_date, bill_number, note) VALUES (?, ?, ?, ?, ?, ?)",
        (party_type, party_name, amount, payment_date, bill_number, note or None),
    )
    db.commit()
    flash("Bill payment recorded", "success")
    return redirect(url_for("ledger_page", view="supplier"))


@app.route("/ledger")
def ledger_page():
    init_db()
    db = get_db()
    view = (request.args.get("view") or "customer").lower()
    if view == "supplier":
        parties = db.execute(
            """
            SELECT
                s.id AS party_id,
                s.name AS party_name,
                SUM(t.total) AS total_value,
                COUNT(t.id) AS bill_count
            FROM suppliers s
            LEFT JOIN transactions t
                ON t.transaction_type = 'purchase' AND t.supplier_name = s.name
            GROUP BY s.id, s.name
            ORDER BY s.name
            """
        ).fetchall()
        details = db.execute(
            """
            SELECT
                s.id AS party_id,
                s.name AS party_name,
                t.id AS transaction_id,
                t.created_at,
                t.item_name,
                t.quantity,
                t.unit_price,
                t.total,
                t.bill_number
            FROM suppliers s
            LEFT JOIN transactions t
                ON t.transaction_type = 'purchase' AND t.supplier_name = s.name
            WHERE s.id IS NOT NULL
            ORDER BY s.name, t.created_at DESC, t.id DESC
            """
        ).fetchall()
    else:
        parties = db.execute(
            """
            SELECT
                c.id AS party_id,
                c.name AS party_name,
                SUM(t.total) AS total_value,
                COUNT(t.id) AS bill_count
            FROM customers c
            LEFT JOIN transactions t
                ON t.transaction_type = 'sale' AND t.customer_name = c.name
            GROUP BY c.id, c.name
            ORDER BY c.name
            """
        ).fetchall()
        details = db.execute(
            """
            SELECT
                c.id AS party_id,
                c.name AS party_name,
                t.id AS transaction_id,
                t.created_at,
                t.item_name,
                t.quantity,
                t.unit_price,
                t.total,
                t.bill_number
            FROM customers c
            LEFT JOIN transactions t
                ON t.transaction_type = 'sale' AND t.customer_name = c.name
            WHERE c.id IS NOT NULL
            ORDER BY c.name, t.created_at DESC, t.id DESC
            """
        ).fetchall()

    payments = db.execute(
        "SELECT party_type, party_name, amount, payment_date, bill_number, note FROM ledger_payments WHERE party_type = ? ORDER BY payment_date DESC, id DESC",
        ("supplier" if view == "supplier" else "customer",),
    ).fetchall()

    customers = db.execute(
        "SELECT name FROM customers ORDER BY name"
    ).fetchall()
    suppliers = db.execute(
        "SELECT name FROM suppliers ORDER BY name"
    ).fetchall()

    party_rows = []
    for party in parties:
        entries = []
        total_value = float(party["total_value"] or 0)
        payment_total = 0.0
        for detail in details:
            if detail["party_id"] != party["party_id"]:
                continue
            if detail["created_at"]:
                entries.append({
                    "kind": "transaction",
                    "transaction_id": detail["transaction_id"],
                    "date": detail["created_at"],
                    "label": detail["item_name"] or "Entry",
                    "amount": float(detail["total"] or 0),
                    "meta": f"Qty: {detail['quantity']} • Unit: {float(detail['unit_price'] or 0):.2f} • Total: {float(detail['total'] or 0):.2f}",
                    "bill_number": detail["bill_number"],
                })
        for payment in payments:
            if payment["party_name"] != party["party_name"]:
                continue
            payment_total += float(payment["amount"] or 0)
            entries.append({
                "kind": "payment",
                "date": payment["payment_date"],
                "label": f"Payment{' for bill ' + payment['bill_number'] if payment['bill_number'] else ''}",
                "amount": -float(payment["amount"] or 0),
                "meta": payment["note"] or "",
                "bill_number": payment["bill_number"],
            })
        entries.sort(key=lambda item: item["date"], reverse=True)
        party_rows.append({
            "party": party,
            "entries": entries,
            "payment_total": round(payment_total, 2),
            "balance": round(total_value - payment_total, 2),
        })

    return render_template("ledger.html", party_rows=party_rows, view=view, customers=customers, suppliers=suppliers)


@app.route("/ledger/transaction/<int:transaction_id>/print")
def ledger_transaction_print(transaction_id):
    init_db()
    db = get_db()
    transaction = db.execute(
        "SELECT id, transaction_type, item_name, quantity, unit_price, total, created_at, customer_name, supplier_name, bill_number, note FROM transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    if not transaction:
        abort(404)

    party_type = "customer" if transaction["transaction_type"] == "sale" else "supplier"
    party_name = transaction["customer_name"] or transaction["supplier_name"] or "Unknown"
    return render_template(
        "ledger_bill_print.html",
        transaction=transaction,
        party_type=party_type,
        party_name=party_name,
    )


@app.route("/ledger/customer/<int:customer_id>/delete", methods=["POST"])
def delete_customer_ledger(customer_id):
    init_db()
    db = get_db()
    customer = db.execute("SELECT id, name FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if not customer:
        abort(404)

    db.execute("DELETE FROM ledger_payments WHERE party_type = 'customer' AND party_name = ?", (customer["name"],))
    db.execute("DELETE FROM transactions WHERE transaction_type = 'sale' AND customer_name = ?", (customer["name"],))
    db.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    db.commit()
    flash("Ledger entry deleted", "success")
    return redirect(url_for("ledger_page", view="customer"))


@app.route("/ledger/supplier/<int:supplier_id>/delete", methods=["POST"])
def delete_supplier_ledger(supplier_id):
    init_db()
    db = get_db()
    supplier = db.execute("SELECT id, name FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
    if not supplier:
        abort(404)

    db.execute("DELETE FROM ledger_payments WHERE party_type = 'supplier' AND party_name = ?", (supplier["name"],))
    db.execute("DELETE FROM transactions WHERE transaction_type = 'purchase' AND supplier_name = ?", (supplier["name"],))
    db.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
    db.commit()
    flash("Ledger entry deleted", "success")
    return redirect(url_for("ledger_page", view="supplier"))


@app.route("/ledger/statement/<party_type>/<int:party_id>")
def ledger_statement_print(party_type, party_id):
    init_db()
    db = get_db()
    party_type = party_type.lower()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    if party_type == "customer":
        party = db.execute(
            "SELECT id, name, phone, address FROM customers WHERE id = ?",
            (party_id,),
        ).fetchone()
        if not party:
            abort(404)
        transactions = db.execute(
            "SELECT id, item_name, quantity, unit_price, total, created_at, bill_number, note FROM transactions WHERE transaction_type = 'sale' AND customer_name = ? ORDER BY created_at DESC, id DESC",
            (party["name"],),
        ).fetchall()
        payments = db.execute(
            "SELECT amount, payment_date, bill_number, note FROM ledger_payments WHERE party_type = 'customer' AND party_name = ? ORDER BY payment_date DESC, id DESC",
            (party["name"],),
        ).fetchall()
        label = "Total Sale"
    else:
        party = db.execute(
            "SELECT id, name, phone, address FROM suppliers WHERE id = ?",
            (party_id,),
        ).fetchone()
        if not party:
            abort(404)
        transactions = db.execute(
            "SELECT id, item_name, quantity, unit_price, total, created_at, bill_number, note FROM transactions WHERE transaction_type = 'purchase' AND supplier_name = ? ORDER BY created_at DESC, id DESC",
            (party["name"],),
        ).fetchall()
        payments = db.execute(
            "SELECT amount, payment_date, bill_number, note FROM ledger_payments WHERE party_type = 'supplier' AND party_name = ? ORDER BY payment_date DESC, id DESC",
            (party["name"],),
        ).fetchall()
        label = "Total Purchase"

    def is_within_range(entry_date):
        if not entry_date:
            return True
        try:
            date_value = datetime.strptime(entry_date.split(" ")[0], "%Y-%m-%d")
        except ValueError:
            return True
        if start_date:
            try:
                start_value = datetime.strptime(start_date, "%Y-%m-%d")
            except ValueError:
                start_value = None
            if start_value and date_value < start_value:
                return False
        if end_date:
            try:
                end_value = datetime.strptime(end_date, "%Y-%m-%d")
            except ValueError:
                end_value = None
            if end_value and date_value > end_value:
                return False
        return True

    entries = []
    total_value = 0.0
    for transaction in transactions:
        if not is_within_range(transaction["created_at"]):
            continue
        total_value += float(transaction["total"] or 0)
        entries.append({
            "kind": "transaction",
            "date": transaction["created_at"],
            "label": transaction["item_name"] or "Entry",
            "amount": float(transaction["total"] or 0),
            "meta": f"Qty: {transaction['quantity']} • Unit: {float(transaction['unit_price'] or 0):.2f} • Total: {float(transaction['total'] or 0):.2f}",
            "bill_number": transaction["bill_number"],
            "note": transaction["note"],
        })

    payment_total = 0.0
    for payment in payments:
        if not is_within_range(payment["payment_date"]):
            continue
        payment_total += float(payment["amount"] or 0)
        entries.append({
            "kind": "payment",
            "date": payment["payment_date"],
            "label": f"Payment{' for bill ' + payment['bill_number'] if payment['bill_number'] else ''}",
            "amount": -float(payment["amount"] or 0),
            "meta": payment["note"] or "",
            "bill_number": payment["bill_number"],
            "note": payment["note"],
        })

    entries.sort(key=lambda item: item["date"], reverse=True)
    return render_template(
        "ledger_statement_print.html",
        party_type=party_type,
        party=party,
        entries=entries,
        total_value=round(total_value, 2),
        payment_total=round(payment_total, 2),
        balance=round(total_value - payment_total, 2),
        label=label,
        start_date=start_date,
        end_date=end_date,
    )


@app.route("/ledger/customer/<int:customer_id>/details")
def ledger_customer_details(customer_id):
    init_db()
    db = get_db()
    customer = db.execute(
        "SELECT id, name, phone, address FROM customers WHERE id = ?",
        (customer_id,),
    ).fetchone()
    if not customer:
        return jsonify({"error": "Customer not found"}), 404

    bills = db.execute(
        "SELECT created_at, item_name, quantity, unit_price, total FROM transactions WHERE transaction_type = 'sale' AND customer_name = ? ORDER BY created_at DESC, id DESC",
        (customer["name"],),
    ).fetchall()

    total_sales = sum(float(row["total"] or 0) for row in bills)
    return jsonify(
        {
            "customer": {
                "id": customer["id"],
                "name": customer["name"],
                "phone": customer["phone"],
                "address": customer["address"],
            },
            "bills": [
                {
                    "created_at": row["created_at"],
                    "item_name": row["item_name"],
                    "quantity": row["quantity"],
                    "unit_price": row["unit_price"],
                    "total": row["total"],
                }
                for row in bills
            ],
            "total_sales": round(total_sales, 2),
            "bill_count": len(bills),
        }
    )


@app.route("/expenses", methods=["GET"])
def expenses_page():
    init_db()
    db = get_db()
    expenses = db.execute(
        "SELECT id, name, amount, expense_date, category, note, entry_type FROM expenses ORDER BY expense_date DESC"
    ).fetchall()
    current_date = datetime.utcnow().strftime("%Y-%m-%d")
    total_expenses = sum(float(row["amount"] or 0) for row in expenses)
    return render_template(
        "expenses.html",
        expenses=expenses,
        total_expenses=total_expenses,
        current_date=current_date,
    )


@app.route("/expenses", methods=["POST"])
def add_expense():
    init_db()
    entry_type = (request.form.get("entry_type") or "expense").strip().lower()

    if entry_type == "salary":
        worker_name = request.form.get("worker_name", "").strip()
        salary_amount = float(request.form.get("salary_amount", 0))
        expense_date = request.form.get("salary_date", "").strip()
        if not worker_name or not expense_date or salary_amount <= 0:
            flash("Worker name, date, and salary amount are required", "error")
            return redirect(url_for("expenses_page"))
        name = worker_name
        amount = salary_amount
        category = "salary"
        note = "Salary paid"
        saved_entry_type = "salary"
    else:
        name = request.form.get("expense_type", "").strip() or request.form.get("name", "").strip()
        amount = float(request.form.get("expense_amount", request.form.get("amount", 0)))
        expense_date = request.form.get("expense_date", "").strip()
        category = request.form.get("expense_category", "").strip() or "Other"
        note = request.form.get("expense_note", "").strip()
        if not name or not expense_date or amount <= 0:
            flash("Expense type, date, and amount are required", "error")
            return redirect(url_for("expenses_page"))
        saved_entry_type = "expense"

    db = get_db()
    db.execute(
        "INSERT INTO expenses (name, amount, expense_date, category, note, entry_type) VALUES (?, ?, ?, ?, ?, ?)",
        (name, amount, expense_date, category or None, note or None, saved_entry_type),
    )
    db.commit()
    if saved_entry_type == "salary":
        flash(f"Salary paid successfully for {name}", "success")
    else:
        flash("Expense saved successfully", "success")
    return redirect(url_for("expenses_page"))


@app.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
def edit_expense(expense_id):
    init_db()
    db = get_db()
    expense = db.execute(
        "SELECT id, name, amount, expense_date, category, note, entry_type FROM expenses WHERE id = ?",
        (expense_id,),
    ).fetchone()
    if not expense:
        flash("Expense not found", "error")
        return redirect(url_for("expenses_page"))

    if request.method == "POST":
        entry_type = (request.form.get("entry_type") or "expense").strip().lower()
        if entry_type == "salary":
            name = request.form.get("worker_name", "").strip()
            amount = float(request.form.get("salary_amount", 0))
            expense_date = request.form.get("salary_date", "").strip()
            category = "salary"
            note = "Salary paid"
        else:
            name = request.form.get("expense_type", "").strip() or request.form.get("name", "").strip()
            amount = float(request.form.get("expense_amount", request.form.get("amount", 0)))
            expense_date = request.form.get("expense_date", "").strip()
            category = request.form.get("expense_category", "").strip() or "Other"
            note = request.form.get("expense_note", "").strip()
        if not name or not expense_date or amount <= 0:
            flash("Expense details are invalid", "error")
            return redirect(url_for("edit_expense", expense_id=expense_id))
        db.execute(
            "UPDATE expenses SET name = ?, amount = ?, expense_date = ?, category = ?, note = ?, entry_type = ? WHERE id = ?",
            (name, amount, expense_date, category or None, note or None, entry_type, expense_id),
        )
        db.commit()
        flash("Expense updated", "success")
        return redirect(url_for("expenses_page"))

    return jsonify({
        "id": expense["id"],
        "name": expense["name"],
        "amount": expense["amount"],
        "expense_date": expense["expense_date"],
        "category": expense["category"],
        "note": expense["note"],
        "entry_type": expense["entry_type"],
    })


@app.route("/expenses/<int:expense_id>/delete", methods=["POST", "DELETE"])
def delete_expense(expense_id):
    init_db()
    db = get_db()
    expense = db.execute("SELECT id FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not expense:
        flash("Expense not found", "error")
        return redirect(url_for("expenses_page"))
    db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    db.commit()
    flash("Expense deleted", "success")
    return redirect(url_for("expenses_page"))


@app.route("/purchases")
def purchases_page():
    init_db()
    db = get_db()
    transactions = db.execute(
        "SELECT t.id, t.transaction_type, t.item_name, t.quantity, t.unit_price, t.total, t.created_at, t.supplier_name, t.bill_number, t.note, s.id AS supplier_id FROM transactions t LEFT JOIN suppliers s ON s.name = t.supplier_name WHERE t.transaction_type = 'purchase' ORDER BY t.id DESC"
    ).fetchall()
    suppliers = db.execute(
        "SELECT id, name FROM suppliers ORDER BY name"
    ).fetchall()
    flour_total_units = sum(int(row["quantity"]) for row in transactions if row["item_name"] and row["item_name"].strip().lower() == "flour")
    flour_total_kgs = sum(int(row["quantity"]) * KG_PER_PURCHASE_UNIT for row in transactions if row["item_name"] and row["item_name"].strip().lower() == "flour")
    flour_total_amount = sum(float(row["total"] or 0) for row in transactions if row["item_name"] and row["item_name"].strip().lower() == "flour")
    flour_item = get_inventory_item(db, "Flour")
    remaining_flour_kgs = float(flour_item["quantity_on_hand"] or 0) if flour_item else 0.0
    flour_rate = get_latest_flour_purchase_price(db)
    remaining_flour_value = round(remaining_flour_kgs * flour_rate, 2) if flour_rate > 0 else 0.0
    current_date = datetime.utcnow().strftime("%Y-%m-%d")
    return render_template(
        "purchases.html",
        transactions=transactions,
        suppliers=suppliers,
        flour_total_units=flour_total_units,
        flour_total_kgs=flour_total_kgs,
        flour_total_amount=flour_total_amount,
        remaining_flour_kgs=remaining_flour_kgs,
        remaining_flour_value=remaining_flour_value,
        current_date=current_date,
    )


@app.route("/purchases", methods=["POST"])
def add_purchase():
    init_db()
    item_name = "Flour"
    quantity = int(request.form.get("quantity", 0))
    unit_price = float(request.form.get("unit_price", 0))
    supplier_name = request.form.get("supplier_name", "").strip()
    purchase_date = request.form.get("purchase_date", "").strip()
    bill_number = request.form.get("bill_number", "").strip()
    note = request.form.get("note", "").strip()

    if not item_name or quantity <= 0 or unit_price < 0:
        flash("Item name, quantity, and unit price are required", "error")
        return redirect(url_for("purchases_page"))

    db = get_db()
    total = quantity * unit_price
    timestamp = purchase_date or datetime.utcnow().strftime("%Y-%m-%d")
    db.execute(
        "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, supplier_name, bill_number, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("purchase", item_name, quantity, unit_price, total, timestamp, supplier_name or None, bill_number or None, note or None),
    )

    inventory_delta = get_inventory_delta_for_purchase(item_name, quantity)
    existing = get_inventory_item(db, item_name)
    if existing:
        db.execute(
            "UPDATE inventory SET quantity_on_hand = quantity_on_hand + ?, last_purchase_price = ? WHERE id = ?",
            (inventory_delta, unit_price, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO inventory (item_name, quantity_on_hand, last_purchase_price, last_sale_price) VALUES (?, ?, ?, ?)",
            (item_name, inventory_delta, unit_price, 0),
        )
    db.commit()
    flash(f"Purchase added for {item_name}.", "success")
    return redirect(url_for("purchases_page"))


@app.route("/purchases/<int:transaction_id>/edit", methods=["GET", "POST"])
def edit_purchase(transaction_id):
    init_db()
    db = get_db()
    transaction = db.execute(
        "SELECT id, item_name, quantity, unit_price, supplier_name, bill_number, note, created_at FROM transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    if not transaction:
        flash("Purchase not found", "error")
        return redirect(url_for("purchases_page"))

    if request.method == "POST":
        item_name = "Flour"
        quantity = int(request.form.get("quantity", 0))
        unit_price = float(request.form.get("unit_price", 0))
        supplier_name = request.form.get("supplier_name", "").strip()
        purchase_date = request.form.get("purchase_date", "").strip()
        bill_number = request.form.get("bill_number", "").strip()
        note = request.form.get("note", "").strip()

        if not item_name or quantity <= 0 or unit_price < 0:
            flash("Item name, quantity, and unit price are required", "error")
            return redirect(url_for("purchases_page"))

        old_quantity = int(transaction["quantity"])
        old_unit_price = float(transaction["unit_price"] or 0)
        old_inventory_delta = get_inventory_delta_for_purchase(transaction["item_name"], old_quantity)
        old_inventory_item = get_inventory_item(db, transaction["item_name"])
        if old_inventory_item:
            db.execute(
                "UPDATE inventory SET quantity_on_hand = quantity_on_hand - ? WHERE id = ?",
                (old_inventory_delta, old_inventory_item["id"]),
            )
        db.execute(
            "UPDATE transactions SET item_name = ?, quantity = ?, unit_price = ?, total = ?, created_at = ?, supplier_name = ?, bill_number = ?, note = ? WHERE id = ?",
            (item_name, quantity, unit_price, quantity * unit_price, purchase_date or transaction["created_at"], supplier_name or None, bill_number or None, note or None, transaction_id),
        )
        new_inventory_delta = get_inventory_delta_for_purchase(item_name, quantity)
        new_inventory_item = get_inventory_item(db, item_name)
        if new_inventory_item:
            db.execute(
                "UPDATE inventory SET quantity_on_hand = quantity_on_hand + ?, last_purchase_price = ? WHERE id = ?",
                (new_inventory_delta, unit_price, new_inventory_item["id"]),
            )
        else:
            db.execute(
                "INSERT INTO inventory (item_name, quantity_on_hand, last_purchase_price, last_sale_price) VALUES (?, ?, ?, ?)",
                (item_name, new_inventory_delta, unit_price, 0),
            )
        db.commit()
        flash("Purchase updated", "success")
        return redirect(url_for("purchases_page"))

    return jsonify({
        "id": transaction["id"],
        "item_name": transaction["item_name"],
        "quantity": transaction["quantity"],
        "unit_price": transaction["unit_price"],
        "supplier_name": transaction["supplier_name"],
        "bill_number": transaction["bill_number"],
        "note": transaction["note"],
        "created_at": transaction["created_at"],
    })


@app.route("/purchases/<int:transaction_id>/delete", methods=["POST", "DELETE"])
def delete_purchase(transaction_id):
    init_db()
    db = get_db()
    transaction = db.execute(
        "SELECT item_name, quantity FROM transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    if not transaction:
        flash("Purchase not found", "error")
        return redirect(url_for("purchases_page"))

    inventory_delta = get_inventory_delta_for_purchase(transaction["item_name"], transaction["quantity"])
    inventory_item = get_inventory_item(db, transaction["item_name"])
    db.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    if inventory_item:
        db.execute(
            "UPDATE inventory SET quantity_on_hand = quantity_on_hand - ? WHERE id = ?",
            (inventory_delta, inventory_item["id"]),
        )
    db.commit()
    flash("Purchase deleted", "success")
    return redirect(url_for("purchases_page"))


@app.route("/productions")
def productions_page():
    init_db()
    db = get_db()
    transactions = db.execute(
        "SELECT id, item_name, quantity, unit_price, total, created_at FROM transactions WHERE transaction_type = 'production' ORDER BY id DESC"
    ).fetchall()
    flatbread_item = db.execute(
        "SELECT quantity_on_hand FROM inventory WHERE LOWER(item_name) = 'flatbread' LIMIT 1"
    ).fetchone()
    flatbread_available = float(flatbread_item["quantity_on_hand"] or 0) if flatbread_item else 0.0
    current_date = datetime.utcnow().strftime("%Y-%m-%d")
    flatbread_rate = get_flatbread_rate(db)
    flatbread_value = round(flatbread_available * flatbread_rate, 2)
    return render_template(
        "productions.html",
        transactions=transactions,
        flatbread_available=flatbread_available,
        flatbread_rate=flatbread_rate,
        flatbread_value=flatbread_value,
        current_date=current_date,
    )


@app.route("/productions", methods=["POST"])
def add_production():
    init_db()
    production_date = request.form.get("production_date", "").strip()
    flatbread_quantity = int(request.form.get("flatbread_quantity", 0))
    if flatbread_quantity <= 0:
        flash("Flatbread quantity is required", "error")
        return redirect(url_for("productions_page"))

    db = get_db()
    flatbread_rate = get_flatbread_rate(db)
    if flatbread_rate <= 0:
        flash("Unable to calculate flatbread rate without a flour purchase price", "error")
        return redirect(url_for("productions_page"))

    # flour (in kg) needed = flatbread_quantity / FLATBREADS_PER_KG
    flour_used = flatbread_quantity / float(FLATBREADS_PER_KG)
    flour_item = get_inventory_item(db, "Flour")
    if not flour_item or float(flour_item["quantity_on_hand"] or 0) < flour_used:
        flash("Not enough flour stock available for production", "error")
        return redirect(url_for("productions_page"))

    timestamp = production_date or datetime.utcnow().strftime("%Y-%m-%d")
    total = flatbread_quantity * flatbread_rate
    db.execute(
        "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("production", "Flatbread", flatbread_quantity, flatbread_rate, total, timestamp),
    )
    db.execute(
        "UPDATE inventory SET quantity_on_hand = quantity_on_hand - ? WHERE id = ?",
        (flour_used, flour_item["id"]),
    )
    flatbread_item = get_inventory_item(db, "Flatbread")
    if flatbread_item:
        db.execute(
            "UPDATE inventory SET quantity_on_hand = quantity_on_hand + ? WHERE id = ?",
            (flatbread_quantity, flatbread_item["id"]),
        )
    else:
        db.execute(
            "INSERT INTO inventory (item_name, quantity_on_hand, last_purchase_price, last_sale_price) VALUES (?, ?, ?, ?)",
            ("Flatbread", flatbread_quantity, 0, 0),
        )
    db.commit()
    flash(f"Production recorded for {flatbread_quantity} Flatbread", "success")
    return redirect(url_for("productions_page"))


@app.route("/productions/<int:transaction_id>/edit", methods=["GET", "POST"])
def edit_production(transaction_id):
    init_db()
    db = get_db()
    transaction = db.execute(
        "SELECT id, quantity, unit_price, created_at FROM transactions WHERE id = ? AND transaction_type = 'production'",
        (transaction_id,),
    ).fetchone()
    if not transaction:
        flash("Production record not found", "error")
        return redirect(url_for("productions_page"))

    if request.method == "POST":
        production_date = request.form.get("production_date", "").strip()
        flatbread_quantity = int(request.form.get("flatbread_quantity", 0))
        if flatbread_quantity <= 0:
            flash("Flatbread quantity is required", "error")
            return redirect(url_for("productions_page"))

        old_quantity = int(transaction["quantity"])
        old_flour_used = old_quantity / float(FLATBREADS_PER_KG)
        new_flour_used = flatbread_quantity / float(FLATBREADS_PER_KG)
        flour_needed = new_flour_used - old_flour_used

        flour_item = get_inventory_item(db, "Flour")
        if flour_needed > 0 and (not flour_item or float(flour_item["quantity_on_hand"] or 0) < flour_needed):
            flash("Not enough flour stock available for production update", "error")
            return redirect(url_for("productions_page"))

        flatbread_rate = get_flatbread_rate(db) or float(transaction["unit_price"] or 0)
        db.execute(
            "UPDATE transactions SET quantity = ?, unit_price = ?, total = ?, created_at = ? WHERE id = ?",
            (flatbread_quantity, flatbread_rate, flatbread_quantity * flatbread_rate, production_date or transaction["created_at"], transaction_id),
        )

        if flour_needed != 0 and flour_item:
            db.execute(
                "UPDATE inventory SET quantity_on_hand = quantity_on_hand - ? WHERE id = ?",
                (flour_needed, flour_item["id"]),
            )
        if flour_needed > 0 and not flour_item:
            db.execute(
                "INSERT INTO inventory (item_name, quantity_on_hand, last_purchase_price, last_sale_price) VALUES (?, ?, ?, ?)",
                ("Flour", flour_needed, 0, 0),
            )

        flatbread_item = get_inventory_item(db, "Flatbread")
        if flatbread_item:
            db.execute(
                "UPDATE inventory SET quantity_on_hand = quantity_on_hand + ? WHERE id = ?",
                (flatbread_quantity - old_quantity, flatbread_item["id"]),
            )
        else:
            db.execute(
                "INSERT INTO inventory (item_name, quantity_on_hand, last_purchase_price, last_sale_price) VALUES (?, ?, ?, ?)",
                ("Flatbread", flatbread_quantity, 0, 0),
            )
        db.commit()
        flash("Production updated", "success")
        return redirect(url_for("productions_page"))

    return jsonify(
        {
            "id": transaction["id"],
            "production_date": transaction["created_at"],
            "flatbread_quantity": transaction["quantity"],
        }
    )


@app.route("/productions/<int:transaction_id>/delete", methods=["POST", "DELETE"])
def delete_production(transaction_id):
    init_db()
    db = get_db()
    transaction = db.execute(
        "SELECT id, quantity FROM transactions WHERE id = ? AND transaction_type = 'production'",
        (transaction_id,),
    ).fetchone()
    if not transaction:
        flash("Production record not found", "error")
        return redirect(url_for("productions_page"))

    flatbread_quantity = int(transaction["quantity"])
    # restored flour in kg = flatbread_quantity / FLATBREADS_PER_KG
    flour_restored = flatbread_quantity / float(FLATBREADS_PER_KG)

    db.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))

    flour_item = get_inventory_item(db, "Flour")
    if flour_item:
        db.execute(
            "UPDATE inventory SET quantity_on_hand = quantity_on_hand + ? WHERE id = ?",
            (flour_restored, flour_item["id"]),
        )
    else:
        db.execute(
            "INSERT INTO inventory (item_name, quantity_on_hand, last_purchase_price, last_sale_price) VALUES (?, ?, ?, ?)",
            ("Flour", flour_restored, 0, 0),
        )

    flatbread_item = get_inventory_item(db, "Flatbread")
    if flatbread_item:
        new_qty = float(flatbread_item["quantity_on_hand"] or 0) - flatbread_quantity
        if new_qty <= 0:
            db.execute("DELETE FROM inventory WHERE id = ?", (flatbread_item["id"],))
        else:
            db.execute(
                "UPDATE inventory SET quantity_on_hand = ? WHERE id = ?",
                (new_qty, flatbread_item["id"]),
            )

    db.commit()
    flash("Production deleted", "success")
    return redirect(url_for("productions_page"))


@app.route("/api/summary")
def api_summary():
    init_db()
    return jsonify(get_summary())


@app.route("/api/transactions")
def api_transactions():
    init_db()
    rows = get_db().execute(
        "SELECT id, transaction_type, item_name, quantity, unit_price, total, created_at FROM transactions ORDER BY id DESC LIMIT 50"
    ).fetchall()
    return jsonify([dict(row) for row in rows])


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
