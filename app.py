from flask import Flask, jsonify, request, render_template, make_response
from flask_cors import CORS
import mysql.connector
from mysql.connector import Error
from datetime import datetime

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

DB_CONFIG = {
    "host": "localhost",
    "user": "root",          # change if needed
    "password": "root",      # change if needed
    "database": "invoice_db_v2"
}

def get_connection():
    return mysql.connector.connect(**DB_CONFIG)

@app.route("/")
def index():
    return render_template("index.html")

# ---------- CLIENTS ----------

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
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"success": False, "message": "Invalid JSON payload"}), 400

    name = (data.get("name") or "").strip()
    if len(name) < 2:
        return jsonify({"success": False, "message": "Client name must be at least 2 characters"}), 400

    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        # case-insensitive duplicate check
        cur.execute("SELECT id FROM clients WHERE LOWER(name) = LOWER(%s)", (name,))
        existing = cur.fetchone()
        if existing:
            cur.close()
            conn.close()
            return jsonify({"success": False, "message": "Client already exists"}), 409

        cur.execute("INSERT INTO clients (name) VALUES (%s)", (name,))
        conn.commit()
        client_id = cur.lastrowid
        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Client added successfully",
            "client": {"id": client_id, "name": name}
        }), 201
    except Error as e:
        return jsonify({"success": False, "message": f"DB error: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500

# ---------- INVOICES LIST ----------

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
        invoices = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify({"success": True, "invoices": invoices}), 200
    except Error as e:
        return jsonify({"success": False, "message": f"DB error: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500

# ---------- CREATE INVOICE ----------

@app.route("/api/invoices", methods=["POST"])
def create_invoice():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"success": False, "message": "Invalid JSON payload"}), 400

    # required fields
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

    # calculate totals
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

        # ensure client exists
        cur.execute("SELECT id FROM clients WHERE id = %s", (client_id,))
        if not cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({"success": False, "message": "Client not found"}), 400

        # insert invoice
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

        # insert items
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

# ---------- GET ONE INVOICE ----------

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
        items = cur.fetchall()
        inv["items"] = items

        cur.close()
        conn.close()
        return jsonify({"success": True, "invoice": inv}), 200
    except Error as e:
        return jsonify({"success": False, "message": f"DB error: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500

# ---------- DELETE INVOICE ----------

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

# ---------- DOWNLOAD INVOICE (TXT) ----------

@app.route("/api/invoices/<int:invoice_id>/download", methods=["GET"])
def download_invoice(invoice_id):
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
            SELECT description, quantity, unit_price, gst_percentage
            FROM invoice_items
            WHERE invoice_id = %s
        """, (invoice_id,))
        items = cur.fetchall()
        cur.close()
        conn.close()

        lines = []
        lines.append(f"Invoice Number: {inv['invoice_number']}")
        lines.append(f"Client: {inv['client_name']}")
        lines.append(f"Invoice Date: {inv['invoice_date']}")
        lines.append(f"Due Date: {inv['due_date']}")
        lines.append("")
        lines.append("Items:")
        for it in items:
            lines.append(
                f"- {it['description']} x {it['quantity']} @ ₹{it['unit_price']} + {it['gst_percentage']}% GST"
            )
        lines.append("")
        lines.append(f"Subtotal: ₹{inv['subtotal']}")
        lines.append(f"GST: ₹{inv['tax_total']}")
        lines.append(f"Grand Total: ₹{inv['grand_total']}")
        content = "\n".join(lines)

        resp = make_response(content)
        resp.headers["Content-Type"] = "text/plain; charset=utf-8"
        resp.headers["Content-Disposition"] = f"attachment; filename={inv['invoice_number']}.txt"
        return resp
    except Error as e:
        return jsonify({"success": False, "message": f"DB error: {e}"}), 500
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500

if __name__ == "__main__":
    app.run(debug=True)
