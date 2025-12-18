from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
import mysql.connector
from mysql.connector import Error
from datetime import datetime

app = Flask(__name__)
# Allow front-end on same origin or different port (e.g., 5500, 5173)
CORS(app, resources={r"/api/*": {"origins": "*"}})

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "root",          # UPDATE to your password
    "database": "invoice_db_v2"  # Must match schema.sql
}

def get_connection():
    return mysql.connector.connect(**DB_CONFIG)

@app.route("/")
def index():
    # Make sure templates/index.html exists
    return render_template("index.html")

# ============ CLIENT APIs ============

@app.route("/api/clients", methods=["GET"])
def get_clients():
    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, name, address, email FROM clients ORDER BY name")
        clients = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "clients": clients}), 200
    except Error as e:
        return jsonify({"success": False, "message": f"DB error: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500

@app.route("/api/clients", methods=["POST"])
def add_client():
    try:
        data = request.get_json(force=True)  # force=True: handle missing header gracefully
    except Exception:
        return jsonify({"success": False, "message": "Invalid JSON payload"}), 400

    name = (data.get("name") or "").strip()

    if len(name) < 2:
        return jsonify({
            "success": False,
            "message": "Client name must be at least 2 characters"
        }), 400

    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)

        # Case-insensitive duplicate check
        cur.execute("SELECT id FROM clients WHERE LOWER(name) = LOWER(%s)", (name,))
        existing = cur.fetchone()
        if existing:
            cur.close()
            conn.close()
            return jsonify({"success": False, "message": "Client already exists"}), 409

        cur.execute(
            "INSERT INTO clients (name) VALUES (%s)",
            (name,)
        )
        conn.commit()
        new_id = cur.lastrowid
        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Client added successfully",
            "client": {"id": new_id, "name": name}
        }), 201

    except Error as e:
        # If the table/db is wrong, you will see the exact error in response and console
        return jsonify({"success": False, "message": f"DB error: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500

# ============ INVOICE APIs (unchanged from earlier, but kept complete) ============

@app.route("/api/invoices", methods=["GET"])
def list_invoices():
    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT i.id, i.invoice_number, i.client_id, c.name AS client_name,
                   i.invoice_date, i.due_date, i.status,
                   i.subtotal, i.tax_total, i.grand_total
            FROM invoices i
            JOIN clients c ON i.client_id = c.id
            ORDER BY i.id
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "invoices": rows}), 200
    except Error as e:
        return jsonify({"success": False, "message": f"DB error: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500

@app.route("/api/invoices", methods=["POST"])
def create_invoice():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"success": False, "message": "Invalid JSON payload"}), 400

    # Basic fields
    try:
        client_id = int(data.get("client_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid client id"}), 400

    items = data.get("items") or []
    if not items:
        return jsonify({"success": False, "message": "At least one item is required"}), 400

    invoice_date = data.get("invoice_date")
    due_date = data.get("due_date")
    status = (data.get("status") or "Draft").strip()
    billing_address = (data.get("billing_address") or "").strip()
    customer_email = (data.get("customer_email") or None)
    notes = (data.get("notes") or None)

    try:
        datetime.strptime(invoice_date, "%Y-%m-%d")
        datetime.strptime(due_date, "%Y-%m-%d")
    except Exception:
        return jsonify({"success": False, "message": "Invalid date format (use YYYY-MM-DD)"}), 400

    # Totals
    subtotal = 0.0
    tax_total = 0.0
    try:
        for it in items:
            qty = float(it.get("quantity", 0))
            price = float(it.get("unit_price", 0))
            gst = float(it.get("gst_percentage", 0))
            line = qty * price
            gst_amount = line * gst / 100.0
            subtotal += line
            tax_total += gst_amount
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid item numeric values"}), 400

    grand_total = subtotal + tax_total

    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)

        # Verify client exists
        cur.execute("SELECT id FROM clients WHERE id = %s", (client_id,))
        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"success": False, "message": "Client not found"}), 400

        # Insert invoice (invoice_number generated after ID known)
        cur.execute("""
            INSERT INTO invoices
            (client_id, invoice_date, due_date, status,
             billing_address, customer_email, notes,
             subtotal, tax_total, grand_total)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (client_id, invoice_date, due_date, status,
              billing_address, customer_email, notes,
              subtotal, tax_total, grand_total))
        conn.commit()
        invoice_id = cur.lastrowid
        invoice_number = f"INV-{invoice_id:05d}"

        cur.execute("UPDATE invoices SET invoice_number=%s WHERE id=%s",
                    (invoice_number, invoice_id))
        conn.commit()

        # Insert line items
        for it in items:
            desc = (it.get("description") or "").strip()
            qty = float(it.get("quantity", 0))
            price = float(it.get("unit_price", 0))
            gst = float(it.get("gst_percentage", 0))
            cur.execute("""
                INSERT INTO invoice_items
                (invoice_id, description, quantity, unit_price, gst_percentage)
                VALUES (%s,%s,%s,%s,%s)
            """, (invoice_id, desc, qty, price, gst))
        conn.commit()

        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Invoice created successfully",
            "invoice_id": invoice_id,
            "invoice_number": invoice_number
        }), 201

    except Error as e:
        return jsonify({"success": False, "message": f"DB error: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500

@app.route("/api/invoices/<int:invoice_id>", methods=["GET"])
def get_invoice(invoice_id):
    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT i.*, c.name AS client_name
            FROM invoices i
            JOIN clients c ON i.client_id = c.id
            WHERE i.id = %s
        """, (invoice_id,))
        inv = cur.fetchone()
        if not inv:
            cur.close()
            conn.close()
            return jsonify({"success": False, "message": "Invoice not found"}), 404

        cur.execute("""
            SELECT id, description, quantity, unit_price, gst_percentage
            FROM invoice_items
            WHERE invoice_id = %s
        """, (invoice_id,))
        inv["items"] = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "invoice": inv}), 200
    except Error as e:
        return jsonify({"success": False, "message": f"DB error: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500

@app.route("/api/invoices/<int:invoice_id>", methods=["DELETE"])
def delete_invoice(invoice_id):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM invoice_items WHERE invoice_id = %s", (invoice_id,))
        cur.execute("DELETE FROM invoices WHERE id = %s", (invoice_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "message": "Invoice deleted successfully"}), 200
    except Error as e:
        return jsonify({"success": False, "message": f"DB error: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500

if __name__ == "__main__":
    app.run(debug=True)
