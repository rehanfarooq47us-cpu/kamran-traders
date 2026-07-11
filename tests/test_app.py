import unittest

from app import app, get_db, init_db


class SalesPurchaseAppTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        app.config["TESTING"] = True
        with app.app_context():
            init_db()
            db = get_db()
            db.execute("DELETE FROM expenses")
            db.execute("DELETE FROM transactions")
            db.execute("DELETE FROM customers")
            db.execute("DELETE FROM suppliers")
            db.execute("DELETE FROM inventory")
            db.execute("DELETE FROM ledger_payments")
            db.commit()

    def test_init_db_works_without_request_context(self):
        with app.app_context():
            init_db()

    def test_sidebar_pages_load(self):
        for path in ["/purchases", "/sales", "/productions", "/customers", "/suppliers", "/expenses", "/ledger"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, msg=path)

    def test_productions_page_loads(self):
        response = self.client.get("/productions")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Production", response.data)

    def test_login_redirects_to_purchases(self):
        response = self.client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Purchases", response.data)

    def test_purchase_and_sale_flow(self):
        purchase_response = self.client.post(
            "/purchases",
            data={
                "item_name": "Flour",
                "quantity": 1,
                "unit_price": 80,
                "supplier_name": "ABC Traders",
            },
            follow_redirects=True,
        )
        self.assertEqual(purchase_response.status_code, 200)
        self.assertIn(b"Purchase added for Flour", purchase_response.data)

        sale_response = self.client.post(
            "/sales",
            data={"item_name": "Flour", "quantity": 2, "unit_price": 3.5, "customer_name": "Ali"},
            follow_redirects=True,
        )
        self.assertEqual(sale_response.status_code, 200)

        summary = self.client.get("/api/summary").get_json()
        self.assertEqual(summary["total_purchases"], 80.0)
        self.assertEqual(summary["total_sales"], 7.0)
        self.assertEqual(summary["inventory_items"], 1)
        # 1 purchase unit = 15 kg, then sold 2 kg -> remaining 13 kg
        self.assertEqual(summary["stock_units"], 13)

    def test_save_and_print_sale_creates_receipt(self):
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO customers (name) VALUES (?)", ("Ali",))
            db.execute("INSERT INTO inventory (item_name, quantity_on_hand, last_purchase_price, last_sale_price) VALUES (?, ?, ?, ?)", ("Flatbread", 10, 5, 0))
            db.commit()

        response = self.client.post(
            "/sales",
            data={
                "sale_date": "2026-07-05",
                "customer_name": "Ali",
                "item_name": "Flatbread",
                "quantity": 2,
                "unit_price": 5,
                "total_amount": 10,
                "action": "save_print",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/sales/receipt/", response.headers["Location"])

    def test_sales_page_shows_available_flatbread_card_and_limits_sales(self):
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO inventory (item_name, quantity_on_hand, last_purchase_price, last_sale_price) VALUES (?, ?, ?, ?)", ("Flatbread", 5, 5, 0))
            db.commit()

        response = self.client.get("/sales")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Available Flatbread", response.data)

        sale_response = self.client.post(
            "/sales",
            data={
                "customer_name": "Ali",
                "item_name": "Flatbread",
                "quantity": 6,
                "unit_price": 5,
            },
            follow_redirects=True,
        )
        self.assertEqual(sale_response.status_code, 200)
        self.assertIn(b"Not enough stock available for this sale", sale_response.data)

        with app.app_context():
            db = get_db()
            sale_count = db.execute("SELECT COUNT(*) FROM transactions WHERE transaction_type = 'sale'").fetchone()[0]
            self.assertEqual(sale_count, 0)

    def test_customer_and_supplier_detail_pages_show_records(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO customers (name, phone, email, address) VALUES (?, ?, ?, ?)",
                ("Ali Khan", "03001234567", "ali@example.com", "Lahore"),
            )
            db.execute(
                "INSERT INTO suppliers (name, phone, email, address) VALUES (?, ?, ?, ?)",
                ("ABC Traders", "03009876543", "abc@example.com", "Karachi"),
            )
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, customer_name, supplier_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("sale", "Rice", 2, 120, 240, "2026-07-01 10:00:00", "Ali Khan", None),
            )
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, customer_name, supplier_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("purchase", "Sugar", 5, 80, 400, "2026-07-02 11:00:00", None, "ABC Traders"),
            )
            db.commit()
            customer_id = db.execute("SELECT id FROM customers WHERE name = ?", ("Ali Khan",)).fetchone()[0]
            supplier_id = db.execute("SELECT id FROM suppliers WHERE name = ?", ("ABC Traders",)).fetchone()[0]

        customer_page = self.client.get(f"/customers/{customer_id}")
        supplier_page = self.client.get(f"/suppliers/{supplier_id}")
        self.assertEqual(customer_page.status_code, 200)
        self.assertEqual(supplier_page.status_code, 200)
        self.assertIn(b"Ali Khan", customer_page.data)
        self.assertIn(b"ABC Traders", supplier_page.data)

    def test_add_expense_records_expenses(self):
        response = self.client.post(
            "/expenses",
            data={"name": "Rent", "amount": 2500, "expense_date": "2026-07-01", "category": "Office", "note": "Monthly"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Expense saved successfully", response.data)

    def test_expense_and_salary_entries_update_summary_totals(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("sale", "Rice", 2, 120, 240, "2026-07-01"),
            )
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("purchase", "Flour", 1, 80, 80, "2026-07-01"),
            )
            db.commit()

        expense_response = self.client.post(
            "/expenses",
            data={"entry_type": "expense", "expense_type": "Rent", "expense_amount": 500, "expense_date": "2026-07-01"},
            follow_redirects=True,
        )
        self.assertEqual(expense_response.status_code, 200)

        salary_response = self.client.post(
            "/expenses",
            data={"entry_type": "salary", "salary_date": "2026-07-02", "worker_name": "Ali", "salary_amount": 1000},
            follow_redirects=True,
        )
        self.assertEqual(salary_response.status_code, 200)

        summary = self.client.get("/api/summary").get_json()
        self.assertEqual(summary["total_expenses"], 1500.0)
        self.assertEqual(summary["profit"], -1340.0)

    def test_ledger_payment_entries_update_outstanding_balances(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)",
                ("Ali Khan", "03001234567", "Lahore"),
            )
            db.execute(
                "INSERT INTO suppliers (name, phone, address) VALUES (?, ?, ?)",
                ("ABC Traders", "03009876543", "Karachi"),
            )
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, customer_name, supplier_name, bill_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("sale", "Rice", 2, 120, 240, "2026-07-01", "Ali Khan", None, None),
            )
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, customer_name, supplier_name, bill_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("purchase", "Sugar", 5, 80, 400, "2026-07-02", None, "ABC Traders", "B-100"),
            )
            db.commit()

        payment_response = self.client.post(
            "/ledger/payment",
            data={"party_type": "customer", "party_name": "Ali Khan", "payment_date": "2026-07-01", "amount": 50},
            follow_redirects=True,
        )
        self.assertEqual(payment_response.status_code, 200)

        bill_payment_response = self.client.post(
            "/ledger/pay-bill",
            data={"party_type": "supplier", "party_name": "ABC Traders", "payment_date": "2026-07-02", "bill_number": "B-100", "amount": 100},
            follow_redirects=True,
        )
        self.assertEqual(bill_payment_response.status_code, 200)

        with app.app_context():
            db = get_db()
            customer_payment = db.execute("SELECT COUNT(*) FROM ledger_payments WHERE party_type = 'customer' AND party_name = ?", ("Ali Khan",)).fetchone()[0]
            supplier_payment = db.execute("SELECT COUNT(*) FROM ledger_payments WHERE party_type = 'supplier' AND party_name = ?", ("ABC Traders",)).fetchone()[0]
            self.assertEqual(customer_payment, 1)
            self.assertEqual(supplier_payment, 1)

    def test_ledger_statement_print_supports_date_range_filter(self):
        with app.app_context():
            db = get_db()
            cursor = db.execute("INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)", ("Ali Khan", "03001234567", "Lahore"))
            customer_id = cursor.lastrowid
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, customer_name, supplier_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("sale", "Rice", 2, 120, 240, "2026-07-01", "Ali Khan", None),
            )
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, customer_name, supplier_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("sale", "Wheat", 1, 80, 80, "2026-07-03", "Ali Khan", None),
            )
            db.commit()

        response = self.client.get(f"/ledger/statement/customer/{customer_id}?start_date=2026-07-01&end_date=2026-07-02")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Rice", response.data)
        self.assertNotIn(b"Wheat", response.data)

    def test_ledger_view_toggle_and_customer_detail_data(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)",
                ("Ali Khan", "03001234567", "Lahore"),
            )
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, customer_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("sale", "Rice", 2, 120, 240, "2026-07-01", "Ali Khan"),
            )
            db.commit()

        response = self.client.get("/ledger?view=customer")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Customer Ledger", response.data)
        self.assertIn(b"Ali Khan", response.data)

    def test_dashboard_shows_credit_and_payment_metrics(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, customer_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("sale", "Rice", 2, 120, 240, "2026-07-01", "Ali Khan"),
            )
            db.execute(
                "INSERT INTO ledger_payments (party_type, party_name, amount, payment_date, note) VALUES (?, ?, ?, ?, ?)",
                ("customer", "Ali Khan", 50, "2026-07-01", "Advance"),
            )
            db.commit()

        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Credit", response.data)
        self.assertIn(b"Payments", response.data)

    def test_ledger_page_shows_collapsible_party_actions(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)",
                ("Ali Khan", "03001234567", "Lahore"),
            )
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, customer_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("sale", "Flatbread", 1, 5, 5, "2026-07-01", "Ali Khan"),
            )
            db.commit()

        response = self.client.get("/ledger?view=customer")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Statement", response.data)
        self.assertIn(b"Print", response.data)

    def test_sidebar_has_theme_lamp_toggle(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"theme-lamp-toggle", response.data)

    def test_sidebar_pages_include_theme_script(self):
        for path in ["/dashboard", "/productions", "/purchases", "/sales", "/expenses", "/ledger"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, msg=path)
            self.assertIn(b"static/app.js", response.data, msg=path)

    def test_removed_pages_are_not_available(self):
        for path in ["/categories", "/products", "/invoices", "/reports", "/salaries"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, msg=path)

    def test_purchase_edit_and_delete_flow(self):
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO transactions (transaction_type, item_name, quantity, unit_price, total, created_at, supplier_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("purchase", "Flour", 1, 80, 80, "2026-07-03", "ABC Traders"),
            )
            db.execute(
                "INSERT INTO inventory (item_name, quantity_on_hand, last_purchase_price, last_sale_price) VALUES (?, ?, ?, ?)",
                ("Flour", 15, 80, 0),
            )
            db.commit()

        edit_response = self.client.post(
            "/purchases/1/edit",
            data={"item_name": "Flour", "quantity": 2, "unit_price": 85, "supplier_name": "ABC Traders", "purchase_date": "2026-07-03"},
            follow_redirects=True,
        )
        self.assertEqual(edit_response.status_code, 200)

        delete_response = self.client.post("/purchases/1/delete", follow_redirects=True)
        self.assertEqual(delete_response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
