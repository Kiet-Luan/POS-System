#!/usr/bin/env python3
"""
Web front end for the simple POS system.

This Flask application provides a basic graphical interface for the
inventory management and record keeping features implemented in
``pos_system.py``.  The goal is to give users an experience closer
to modern POS products like Square without requiring any external
services.  The app serves HTML pages using Bootstrap for styling and
interacts with the same SQLite database used by the command‑line
utility.

Key pages:

* **Home** (`/`) – Lists items in the inventory and allows the
  operator to sell a given quantity of each item.  The available
  stock and price are displayed alongside each product.
* **Add Item** (`/add-item`) – Presents a form to create a new
  inventory record by specifying name, price and quantity.
* **Sales History** (`/sales`) – Shows a table of all recorded
  transactions with timestamps and totals.

Running locally
---------------

1. Ensure you have Python 3 installed.  Install Flask if it's not
   already available: ``pip install flask``.
2. Place ``pos_frontend.py`` and ``pos_system.py`` in the same
   directory.  The first web request will automatically create the
   database if it does not exist.
3. Start the server by executing ``python pos_frontend.py``.  By
   default the application will listen on ``localhost:5000``.  Open
   that URL in your browser to access the interface.

While Square supports features such as bulk import, item modifiers
and low‑stock alerts【747582454232328†L202-L211】, this minimal front end focuses
on core operations—adding items, selling them and reviewing sales
history—so you can run it comfortably in a local development
environment.
"""

import os
import sqlite3
from typing import Optional

try:
    import psycopg2  # type: ignore
    import psycopg2.extras  # type: ignore
    HAS_PSYCOPG2 = True
except ImportError:
    # psycopg2 may not be installed; use SQLite as fallback
    HAS_PSYCOPG2 = False
from datetime import datetime
from typing import Optional, List

from flask import Flask, render_template, request, redirect, url_for, flash, session

DB_FILENAME = "pos.db"

# Directory where uploaded images will be stored.  When running the
# application for the first time, this directory will be created
# automatically if it does not exist.
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def get_connection() -> sqlite3.Connection:
    """Return a database connection to either PostgreSQL (RDS) or SQLite.

    If environment variables for a PostgreSQL database are set (DB_HOST, DB_NAME,
    DB_USER, DB_PASSWORD), this function connects using psycopg2.  Otherwise,
    it falls back to the local SQLite database file.  After connecting,
    create_tables() is called to ensure required tables and columns exist.
    """
    # Determine whether to use PostgreSQL based on environment variables
    use_pg = (
        HAS_PSYCOPG2
        and os.environ.get("DB_HOST")
        and os.environ.get("DB_NAME")
        and os.environ.get("DB_USER")
        and os.environ.get("DB_PASSWORD")
    )
    if use_pg:
        # Connect to PostgreSQL using credentials from environment variables
        conn = psycopg2.connect(
            host=os.environ["DB_HOST"],
            port=os.environ.get("DB_PORT", "5432"),
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
        )
        # Use a DictCursor to return rows like dictionaries
        conn.cursor_factory = psycopg2.extras.DictCursor  # type: ignore
        # Ensure tables exist
        create_tables(conn)
        return conn
    else:
        # Fallback to SQLite
        conn = sqlite3.connect(DB_FILENAME)
        conn.row_factory = sqlite3.Row
        create_tables(conn)
        return conn


def create_tables(conn) -> None:
    """Create required tables and columns for either SQLite or PostgreSQL.

    This function runs `CREATE TABLE IF NOT EXISTS` statements for the
    `inventory` and `sales` tables.  It also adds additional columns using
    `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for PostgreSQL or checks
    existing columns for SQLite.  No error is raised if the columns already
    exist.
    """
    # Determine if we are using PostgreSQL based on module availability and connection type
    is_pg = HAS_PSYCOPG2 and isinstance(conn, psycopg2.extensions.connection)  # type: ignore
    cur = conn.cursor()
    if is_pg:
        # Create tables with Postgres syntax
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                image_path TEXT,
                favorite INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sales (
                id SERIAL PRIMARY KEY,
                item_id INTEGER NOT NULL REFERENCES inventory(id),
                quantity INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                total_price REAL NOT NULL,
                cancelled INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Add columns if missing (PostgreSQL 9.6+ supports IF NOT EXISTS)
        cur.execute("ALTER TABLE inventory ADD COLUMN IF NOT EXISTS image_path TEXT")
        cur.execute("ALTER TABLE inventory ADD COLUMN IF NOT EXISTS favorite INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE sales ADD COLUMN IF NOT EXISTS cancelled INTEGER DEFAULT 0")
        conn.commit()
    else:
        # SQLite
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                image_path TEXT,
                favorite INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                total_price REAL NOT NULL,
                cancelled INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(item_id) REFERENCES inventory(id)
            )
            """
        )
        conn.commit()
        # Ensure additional columns exist when upgrading
        # SQLite does not support IF NOT EXISTS on ADD COLUMN, so we must check
        cur.execute("PRAGMA table_info(inventory)")
        columns = [row[1] for row in cur.fetchall()]
        if "image_path" not in columns:
            cur.execute("ALTER TABLE inventory ADD COLUMN image_path TEXT")
        if "favorite" not in columns:
            cur.execute("ALTER TABLE inventory ADD COLUMN favorite INTEGER DEFAULT 0")
        cur.execute("PRAGMA table_info(sales)")
        sales_cols = [row[1] for row in cur.fetchall()]
        if "cancelled" not in sales_cols:
            cur.execute("ALTER TABLE sales ADD COLUMN cancelled INTEGER DEFAULT 0")
        conn.commit()


def list_inventory(conn, search_term: Optional[str] = None):
    """Return a list of inventory items, optionally filtered by name.

    Items are sorted alphabetically with favorites first.  When using
    PostgreSQL, case‑insensitive search and ordering are achieved using
    ILIKE and the default collation; when using SQLite, LIKE with
    `COLLATE NOCASE` is used.
    """
    cur = conn.cursor()
    is_pg = HAS_PSYCOPG2 and isinstance(conn, psycopg2.extensions.connection)  # type: ignore
    query = "SELECT id, name, price, quantity, image_path, favorite FROM inventory"
    params: list = []
    if search_term:
        if is_pg:
            query += " WHERE name ILIKE %s"
        else:
            query += " WHERE name LIKE ? COLLATE NOCASE"
        params.append(f"%{search_term}%")
    # Ordering
    if is_pg:
        query += " ORDER BY favorite DESC, name"
    else:
        query += " ORDER BY favorite DESC, name COLLATE NOCASE"
    # Replace placeholders for Postgres
    if is_pg:
        query_exec = query
    else:
        query_exec = query
    cur.execute(query_exec.replace("?", "%s") if is_pg else query_exec, params)
    return cur.fetchall()


def add_item_db(conn: sqlite3.Connection, name: str, price: float, quantity: int) -> None:
    c = conn.cursor()
    c.execute(
        "INSERT INTO inventory (name, price, quantity) VALUES (?, ?, ?)",
        (name.strip(), price, quantity),
    )
    conn.commit()

def add_item_with_image(conn: sqlite3.Connection, name: str, price: float, quantity: int, image_path: Optional[str]) -> None:
    """Add an item with an optional image path."""
    c = conn.cursor()
    query = "INSERT INTO inventory (name, price, quantity, image_path) VALUES (?, ?, ?, ?)"
    c.execute(adapt_sql(query, conn), (name.strip(), price, quantity, image_path))
    conn.commit()


def update_item_db(
    conn: sqlite3.Connection,
    item_id: int,
    name: Optional[str] = None,
    price: Optional[float] = None,
    quantity: Optional[int] = None,
    image_path: Optional[str] = None,
) -> None:
    """Update fields of an existing inventory item.

    Only fields that are not None will be updated.  If image_path is
    provided, the previous image file (if any) should be cleaned up by
    the caller before calling this function.
    """
    c = conn.cursor()
    # Build dynamic set clause
    fields = []
    values: List[object] = []
    if name is not None:
        fields.append("name = ?")
        values.append(name.strip())
    if price is not None:
        fields.append("price = ?")
        values.append(price)
    if quantity is not None:
        fields.append("quantity = ?")
        values.append(quantity)
    if image_path is not None:
        fields.append("image_path = ?")
        values.append(image_path)
    if not fields:
        return
    values.append(item_id)
    set_clause = ", ".join(fields)
    query = f"UPDATE inventory SET {set_clause} WHERE id = ?"
    c.execute(adapt_sql(query, conn), values)
    conn.commit()


def record_sale_db(conn: sqlite3.Connection, item_id: int, quantity: int) -> str:
    """Attempt to record a sale; return a status message."""
    c = conn.cursor()
    # Select item details
    c.execute(adapt_sql("SELECT name, price, quantity FROM inventory WHERE id = ?", conn), (item_id,))
    row = c.fetchone()
    if row is None:
        return f"Item with ID {item_id} does not exist."
    if quantity <= 0:
        return "Quantity must be positive."
    if row["quantity"] < quantity:
        return f"Insufficient stock. Available: {row['quantity']}"
    # Compute total and update inventory
    total = row["price"] * quantity
    # Deduct quantity from inventory
    c.execute(
        adapt_sql("UPDATE inventory SET quantity = quantity - ? WHERE id = ?", conn),
        (quantity, item_id),
    )
    timestamp = datetime.now().isoformat(timespec="seconds")
    c.execute(
        adapt_sql(
            "INSERT INTO sales (item_id, quantity, timestamp, total_price) VALUES (?, ?, ?, ?)",
            conn,
        ),
        (item_id, quantity, timestamp, total),
    )
    conn.commit()
    return f"Sold {quantity} × '{row['name']}' for ${total:.2f} at {timestamp}."


def list_sales(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Return sales records, including cancelled status."""
    c = conn.cursor()
    c.execute(
        """
        SELECT sales.id,
               inventory.name AS item_name,
               sales.quantity,
               sales.total_price,
               sales.timestamp,
               sales.cancelled
        FROM sales
        JOIN inventory ON sales.item_id = inventory.id
        ORDER BY sales.timestamp DESC
        """
    )
    return c.fetchall()


app = Flask(__name__)
app.config["SECRET_KEY"] = "replace-with-a-secure-secret-key"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# Helper to adapt SQL parameter placeholders for PostgreSQL
def adapt_sql(query: str, conn, params_present: bool = True) -> str:
    """Return a SQL statement with correct parameter placeholders.

    SQLite uses '?' as the placeholder, whereas psycopg2 (PostgreSQL) uses '%s'.
    This function replaces every '?' with '%s' when the connection is a
    psycopg2 connection.  If no parameters are supplied (params_present=False),
    the query is returned unchanged.
    """
    is_pg = HAS_PSYCOPG2 and isinstance(conn, psycopg2.extensions.connection)  # type: ignore
    if params_present and is_pg:
        # Replace all '?' with '%s'
        return query.replace("?", "%s")
    return query


@app.route("/")
def index():
    # Search query
    search_term = request.args.get("q", "").strip()
    with get_connection() as conn:
        items = list_inventory(conn, search_term)
        # Separate favorites and others
        favorites = [item for item in items if item["favorite"]]
        nonfavorites = [item for item in items if not item["favorite"]]
        # Build cart details from session
        cart = session.get("cart", {})
        cart_items: List[dict] = []
        cart_total = 0.0
        if cart:
            item_ids = [int(item_id) for item_id in cart.keys()]
            if item_ids:
                placeholders = ",".join(["?"] * len(item_ids))
                c = conn.cursor()
                c.execute(
                    f"SELECT id, name, price FROM inventory WHERE id IN ({placeholders})",
                    item_ids,
                )
                items_map = {row["id"]: row for row in c.fetchall()}
                for item_id_str, qty in cart.items():
                    iid = int(item_id_str)
                    item_row = items_map.get(iid)
                    if item_row:
                        total = item_row["price"] * qty
                        cart_total += total
                        cart_items.append(
                            {
                                "id": iid,
                                "name": item_row["name"],
                                "price": item_row["price"],
                                "quantity": qty,
                                "total": total,
                            }
                        )
        return render_template(
            "index.html",
            favorites=favorites,
            items=nonfavorites,
            cart_items=cart_items,
            cart_total=cart_total,
            search_term=search_term,
        )


@app.route("/sell", methods=["POST"])
def sell():
    item_id = int(request.form.get("item_id"))
    try:
        quantity = int(request.form.get("quantity"))
    except (TypeError, ValueError):
        flash("Quantity must be an integer.", "danger")
        return redirect(url_for("index"))
    with get_connection() as conn:
        msg = record_sale_db(conn, item_id, quantity)
    flash(msg, "info")
    return redirect(url_for("index"))


@app.route("/add-item", methods=["GET", "POST"])
def add_item():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        price_str = request.form.get("price", "").strip()
        qty_str = request.form.get("quantity", "").strip()
        if not name:
            flash("Item name cannot be empty.", "danger")
            return redirect(url_for("add_item"))
        try:
            price = float(price_str)
            quantity = int(qty_str)
        except ValueError:
            flash("Enter valid numeric values for price and quantity.", "danger")
            return redirect(url_for("add_item"))

        # Handle image upload
        image_file = request.files.get("image")
        image_path = None
        if image_file and image_file.filename:
            filename = image_file.filename
            # Validate extension
            ext = filename.rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                flash("Unsupported image type. Allowed types: " + ", ".join(ALLOWED_EXTENSIONS), "danger")
                return redirect(url_for("add_item"))
            # Ensure upload directory exists
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            # Create a unique filename to avoid collisions
            from uuid import uuid4

            unique_name = f"{uuid4().hex}.{ext}"
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
            image_file.save(save_path)
            # Store relative path for use in HTML
            image_path = os.path.join("uploads", unique_name)

        with get_connection() as conn:
            # Use the extended function to support images
            add_item_with_image(conn, name, price, quantity, image_path)
        flash(f"Item '{name}' added successfully.", "success")
        return redirect(url_for("index"))
    return render_template("add_item.html")


@app.route("/sales")
def sales():
    with get_connection() as conn:
        sales_rows = list_sales(conn)
    return render_template("sales.html", sales=sales_rows)


@app.route("/edit-item/<int:item_id>", methods=["GET", "POST"])
def edit_item(item_id: int):
    """View and update an existing item."""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, name, price, quantity, image_path FROM inventory WHERE id = ?",
            (item_id,),
        )
        item = c.fetchone()
        if item is None:
            flash("Item not found.", "danger")
            return redirect(url_for("index"))
        # On POST, perform update
        if request.method == "POST":
            new_name = request.form.get("name", "").strip()
            price_str = request.form.get("price", "").strip()
            qty_str = request.form.get("quantity", "").strip()
            # Basic validation
            if not new_name:
                flash("Name cannot be empty.", "danger")
                return redirect(url_for("edit_item", item_id=item_id))
            try:
                new_price = float(price_str)
                new_qty = int(qty_str)
            except ValueError:
                flash("Enter valid numbers for price and quantity.", "danger")
                return redirect(url_for("edit_item", item_id=item_id))
            # Handle image upload
            image_file = request.files.get("image")
            new_image_path = None
            if image_file and image_file.filename:
                filename = image_file.filename
                ext = filename.rsplit(".", 1)[-1].lower()
                if ext not in ALLOWED_EXTENSIONS:
                    flash(
                        "Unsupported image type. Allowed types: " + ", ".join(ALLOWED_EXTENSIONS),
                        "danger",
                    )
                    return redirect(url_for("edit_item", item_id=item_id))
                # Delete old image if exists
                old_path = item["image_path"]
                if old_path:
                    old_full_path = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), "static", old_path
                    )
                    if os.path.isfile(old_full_path):
                        try:
                            os.remove(old_full_path)
                        except OSError:
                            pass
                os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
                from uuid import uuid4

                unique_name = f"{uuid4().hex}.{ext}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
                image_file.save(save_path)
                new_image_path = os.path.join("uploads", unique_name)
            # Perform the update
            update_item_db(
                conn,
                item_id,
                name=new_name,
                price=new_price,
                quantity=new_qty,
                image_path=new_image_path if image_file and image_file.filename else None,
            )
            flash("Item updated successfully.", "success")
            return redirect(url_for("index"))
        # GET request – render form with current values
        return render_template("edit_item.html", item=item)


# Utility to check allowed filename extension
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/add-to-cart", methods=["POST"])
def add_to_cart():
    """Add an item to the shopping cart stored in the session."""
    item_id = request.form.get("item_id")
    # Default quantity to 1 if not provided
    qty_str = request.form.get("quantity", "1")
    try:
        quantity = int(qty_str)
    except ValueError:
        flash("Invalid quantity.", "danger")
        return redirect(url_for("index"))
    if not item_id:
        flash("No item specified.", "danger")
        return redirect(url_for("index"))
    # Retrieve item name for feedback
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(adapt_sql("SELECT name FROM inventory WHERE id = ?", conn), (int(item_id),))
        row = c.fetchone()
        if row is None:
            flash("Item does not exist.", "danger")
            return redirect(url_for("index"))
        item_name = row["name"]
    cart = session.get("cart", {})
    cart[item_id] = cart.get(item_id, 0) + quantity
    session["cart"] = cart
    flash(f"Added {quantity} × '{item_name}' to cart.", "success")
    return redirect(url_for("index"))


@app.route("/update-cart", methods=["POST"])
def update_cart():
    """Update the quantity of a specific item in the cart or remove it."""
    item_id = request.form.get("item_id")
    if not item_id:
        flash("Invalid cart update request.", "danger")
        return redirect(url_for("index"))
    cart = session.get("cart", {})
    if item_id not in cart:
        flash("Item not in cart.", "danger")
        return redirect(url_for("index"))
    # Check if remove was requested
    if request.form.get("action") == "remove":
        cart.pop(item_id, None)
        session["cart"] = cart
        flash("Item removed from cart.", "info")
        return redirect(url_for("index"))
    # Otherwise update quantity
    qty_str = request.form.get("quantity")
    try:
        new_qty = int(qty_str)
        if new_qty <= 0:
            cart.pop(item_id, None)
            flash("Item removed from cart.", "info")
        else:
            cart[item_id] = new_qty
            flash("Cart updated.", "success")
    except (TypeError, ValueError):
        flash("Invalid quantity.", "danger")
    session["cart"] = cart
    return redirect(url_for("index"))


@app.route("/checkout", methods=["POST"])
def checkout():
    """Finalize the sale of all items in the cart and clear it."""
    cart = session.get("cart", {})
    if not cart:
        flash("Cart is empty; nothing to checkout.", "warning")
        return redirect(url_for("index"))
    messages = []
    with get_connection() as conn:
        for item_id_str, qty in list(cart.items()):
            item_id = int(item_id_str)
            msg = record_sale_db(conn, item_id, qty)
            messages.append(msg)
    session["cart"] = {}
    flash("Checkout complete. " + " ".join(messages), "success")
    return redirect(url_for("index"))


@app.route("/clear-cart", methods=["POST"])
def clear_cart():
    """Empty the current shopping cart.

    This route resets the session cart to an empty dictionary and
    redirects back to the home page with a confirmation message.
    """
    # Remove cart from session or reset to empty
    session["cart"] = {}
    flash("Cart cleared.", "info")
    return redirect(url_for("index"))


@app.route("/toggle-favorite/<int:item_id>", methods=["POST"])
def toggle_favorite(item_id: int):
    """Toggle the favorite status of an inventory item."""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute(adapt_sql("SELECT name, favorite FROM inventory WHERE id = ?", conn), (item_id,))
        row = c.fetchone()
        if row is None:
            flash("Item not found.", "danger")
            return redirect(url_for("index"))
        new_fav = 0 if row["favorite"] else 1
        c.execute(adapt_sql("UPDATE inventory SET favorite = ? WHERE id = ?", conn), (new_fav, item_id))
        conn.commit()
        action = "added to" if new_fav else "removed from"
        flash(f"'{row['name']}' {action} favorites.", "info")
    return redirect(url_for("index"))


# Cancel a sale record and restore inventory
def cancel_sale_db(conn: sqlite3.Connection, sale_id: int) -> str:
    c = conn.cursor()
    c.execute(
        adapt_sql("SELECT cancelled, item_id, quantity FROM sales WHERE id = ?", conn),
        (sale_id,),
    )
    row = c.fetchone()
    if row is None:
        return "Sale not found."
    if row["cancelled"]:
        return "Sale has already been cancelled."
    # Mark as cancelled
    c.execute(adapt_sql("UPDATE sales SET cancelled = 1 WHERE id = ?", conn), (sale_id,))
    # Restore inventory quantity
    c.execute(
        adapt_sql("UPDATE inventory SET quantity = quantity + ? WHERE id = ?", conn),
        (row["quantity"], row["item_id"]),
    )
    conn.commit()
    return "Sale cancelled and inventory restored."


@app.route("/cancel-sale/<int:sale_id>", methods=["POST"])
def cancel_sale(sale_id: int):
    with get_connection() as conn:
        msg = cancel_sale_db(conn, sale_id)
    # Choose category based on message content
    category = "success" if "restored" in msg else "warning"
    flash(msg, category)
    return redirect(url_for("sales"))


# Delete a sale record and optionally adjust inventory
def delete_sale_db(conn: sqlite3.Connection, sale_id: int) -> str:
    c = conn.cursor()
    c.execute(
        adapt_sql("SELECT cancelled, item_id, quantity FROM sales WHERE id = ?", conn),
        (sale_id,),
    )
    row = c.fetchone()
    if row is None:
        return "Sale not found."
    # If the sale was not cancelled, restore inventory before deleting
    if not row["cancelled"]:
        c.execute(
            adapt_sql("UPDATE inventory SET quantity = quantity + ? WHERE id = ?", conn),
            (row["quantity"], row["item_id"]),
        )
    # Delete the sale
    c.execute(adapt_sql("DELETE FROM sales WHERE id = ?", conn), (sale_id,))
    conn.commit()
    return "Sale deleted permanently and inventory adjusted." if not row["cancelled"] else "Sale deleted permanently."


# Uncancel a sale record and deduct inventory
def uncancel_sale_db(conn: sqlite3.Connection, sale_id: int) -> str:
    c = conn.cursor()
    c.execute(
        adapt_sql("SELECT cancelled, item_id, quantity FROM sales WHERE id = ?", conn),
        (sale_id,),
    )
    row = c.fetchone()
    if row is None:
        return "Sale not found."
    if not row["cancelled"]:
        return "Sale is not cancelled."
    # Check if there is enough inventory to reapply the sale
    c.execute(adapt_sql("SELECT quantity FROM inventory WHERE id = ?", conn), (row["item_id"],))
    item_row = c.fetchone()
    if item_row is None:
        return "Associated item not found."
    if item_row["quantity"] < row["quantity"]:
        return "Not enough stock to uncancel this sale."
    # Deduct inventory
    c.execute(
        adapt_sql("UPDATE inventory SET quantity = quantity - ? WHERE id = ?", conn),
        (row["quantity"], row["item_id"]),
    )
    # Mark sale as not cancelled
    c.execute(adapt_sql("UPDATE sales SET cancelled = 0 WHERE id = ?", conn), (sale_id,))
    conn.commit()
    return "Sale un‑cancelled and inventory updated."


@app.route("/delete-sale/<int:sale_id>", methods=["POST"])
def delete_sale(sale_id: int):
    admin_code = request.form.get("admin_code", "").strip()
    if admin_code != "0516":
        flash("Invalid administrative code for deletion.", "danger")
        return redirect(url_for("sales"))
    with get_connection() as conn:
        msg = delete_sale_db(conn, sale_id)
    flash(msg, "success")
    return redirect(url_for("sales"))


@app.route("/uncancel-sale/<int:sale_id>", methods=["POST"])
def uncancel_sale(sale_id: int):
    admin_code = request.form.get("admin_code", "").strip()
    if admin_code != "0516":
        flash("Invalid administrative code for un‑cancel.", "danger")
        return redirect(url_for("sales"))
    with get_connection() as conn:
        msg = uncancel_sale_db(conn, sale_id)
    category = "success" if msg.startswith("Sale un") else "warning"
    flash(msg, category)
    return redirect(url_for("sales"))


if __name__ == "__main__":
    # Start the development server
    app.run(debug=True)