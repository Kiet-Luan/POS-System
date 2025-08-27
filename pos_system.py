#!/usr/bin/env python3
"""
Simple Point‑of‑Sale (POS) system
=================================

This script implements a minimal POS system inspired by the core
capabilities of Square.  It is designed to run locally on a
developer's machine (for example from within VS Code) and requires
only Python's standard library.  The goal is to provide a small,
self‑contained application that demonstrates the following features:

* **Inventory management** – The POS maintains a catalogue of items
  available for sale.  Each item has a name, unit price and
  quantity on hand.  Inventory counts are automatically updated when
  sales are recorded.

* **Record keeping** – Every sale is logged in a separate table along
  with the item sold, quantity, timestamp and total sale price.  This
  log can be viewed to understand sales history.

* **New item creation** – Users can add new products to the
  inventory, specifying a name, price and starting stock.  Items can
  also be edited or removed if required.

This code is intended for educational purposes and does not handle
payments or integrate with any external hardware.  It stores data
locally in a SQLite database (created automatically on first run) and
provides a simple text‑based menu for interaction.  To run the
application, execute the script with `python pos_system.py` from a
terminal.  The interface will prompt for actions such as adding
items, making sales and viewing reports.

Square's own documentation notes that their platform includes tools
for tracking stock counts, low‑stock alerts and bulk imports, and
allows items to be added individually or via spreadsheets【349903062784324†L35-L83】【747582454232328†L202-L211】.  This program
implements a lightweight version of those capabilities suitable for a
local environment.
"""

import sqlite3
import os

# Attempt to import psycopg2 for PostgreSQL support.  If not available or
# environment variables are not set, the system will fall back to SQLite.
try:
    import psycopg2  # type: ignore
    import psycopg2.extras  # type: ignore
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
import os
from datetime import datetime
from typing import Optional, List, Tuple


DB_FILENAME = "pos.db"


def adapt_sql(query: str, conn, params_present: bool = True) -> str:
    """Return a SQL statement with correct parameter placeholders.

    SQLite uses '?' for parameters, whereas psycopg2 (PostgreSQL) uses '%s'.
    This helper replaces '?' with '%s' when using a psycopg2 connection.
    """
    is_pg = HAS_PSYCOPG2 and isinstance(conn, psycopg2.extensions.connection)  # type: ignore
    if params_present and is_pg:
        return query.replace("?", "%s")
    return query


def get_connection(db_path: str = DB_FILENAME):
    """Return a connection to either PostgreSQL (RDS) or the local SQLite database.

    The database type is determined by the presence of environment variables
    ``DB_HOST``, ``DB_NAME``, ``DB_USER`` and ``DB_PASSWORD``.  If these
    variables are defined and psycopg2 is installed, the function connects
    using ``psycopg2``.  Otherwise it falls back to SQLite.  After opening
    the connection, ``create_tables()`` is called to ensure required tables
    exist.
    """
    use_pg = (
        HAS_PSYCOPG2
        and os.environ.get("DB_HOST")
        and os.environ.get("DB_NAME")
        and os.environ.get("DB_USER")
        and os.environ.get("DB_PASSWORD")
    )
    if use_pg:
        conn = psycopg2.connect(
            host=os.environ["DB_HOST"],
            port=os.environ.get("DB_PORT", "5432"),
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
        )
        conn.cursor_factory = psycopg2.extras.DictCursor  # type: ignore
        create_tables(conn)
        return conn
    else:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        create_tables(conn)
        return conn


def create_tables(conn) -> None:
    """Create required tables for inventory and sales for SQLite or PostgreSQL."""
    is_pg = HAS_PSYCOPG2 and isinstance(conn, psycopg2.extensions.connection)  # type: ignore
    cur = conn.cursor()
    if is_pg:
        # PostgreSQL table definitions
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL
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
                total_price REAL NOT NULL
            )
            """
        )
        conn.commit()
    else:
        # SQLite definitions
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                quantity INTEGER NOT NULL
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
                FOREIGN KEY(item_id) REFERENCES inventory(id)
            )
            """
        )
        conn.commit()


def add_item(conn: sqlite3.Connection, name: str, price: float, quantity: int) -> None:
    """Add a new item to the inventory."""
    cursor = conn.cursor()
    query = "INSERT INTO inventory (name, price, quantity) VALUES (?, ?, ?)"
    cursor.execute(adapt_sql(query, conn), (name.strip(), price, quantity))
    conn.commit()
    print(f"Item '{name}' added with price ${price:.2f} and quantity {quantity}.")


def list_inventory(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Return a list of all items in the inventory."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, quantity FROM inventory ORDER BY id")
    items = cursor.fetchall()
    return items


def view_inventory(conn: sqlite3.Connection) -> None:
    """Display the inventory to the user."""
    items = list_inventory(conn)
    if not items:
        print("\nInventory is empty. Use option 1 to add items.\n")
        return
    print("\nCurrent Inventory:\n")
    print(f"{'ID':<5}{'Name':<20}{'Price':<10}{'Quantity':<10}")
    print("-" * 45)
    for item in items:
        print(f"{item['id']:<5}{item['name']:<20}${item['price']:<9.2f}{item['quantity']:<10}")
    print()


def record_sale(conn: sqlite3.Connection, item_id: int, quantity: int) -> None:
    """Record a sale of an item and update inventory.

    Args:
        conn: Database connection.
        item_id: ID of the item to sell.
        quantity: Quantity sold.
    """
    cursor = conn.cursor()
    # Check if item exists and has enough stock
    cursor.execute(
        adapt_sql("SELECT name, price, quantity FROM inventory WHERE id = ?", conn),
        (item_id,),
    )
    row = cursor.fetchone()
    if row is None:
        print(f"No item found with ID {item_id}.")
        return
    if quantity <= 0:
        print("Quantity must be positive.")
        return
    if row["quantity"] < quantity:
        print(f"Insufficient stock. Available quantity: {row['quantity']}.")
        return
    # Calculate total
    total_price = row["price"] * quantity
    # Update inventory
    cursor.execute(
        adapt_sql("UPDATE inventory SET quantity = quantity - ? WHERE id = ?", conn),
        (quantity, item_id),
    )
    # Insert sale record
    timestamp = datetime.now().isoformat(timespec="seconds")
    cursor.execute(
        adapt_sql(
            "INSERT INTO sales (item_id, quantity, timestamp, total_price) VALUES (?, ?, ?, ?)",
            conn,
        ),
        (item_id, quantity, timestamp, total_price),
    )
    conn.commit()
    print(
        f"Sold {quantity}x '{row['name']}' for ${total_price:.2f} at {timestamp}."
    )


def view_sales(conn: sqlite3.Connection) -> None:
    """Display sales records."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT sales.id, inventory.name, sales.quantity, sales.total_price, sales.timestamp
        FROM sales
        JOIN inventory ON sales.item_id = inventory.id
        ORDER BY sales.timestamp
        """
    )
    rows = cursor.fetchall()
    if not rows:
        print("\nNo sales have been recorded yet.\n")
        return
    print("\nSales History:\n")
    print(f"{'Sale ID':<8}{'Item':<20}{'Qty':<6}{'Total':<12}{'Timestamp':<20}")
    print("-" * 68)
    for row in rows:
        print(
            f"{row['id']:<8}{row['name']:<20}{row['quantity']:<6}${row['total_price']:<11.2f}{row['timestamp']:<20}"
        )
    print()


def update_item(conn: sqlite3.Connection, item_id: int, name: Optional[str], price: Optional[float], quantity: Optional[int]) -> None:
    """Update fields of an existing item. Only supplied values are changed."""
    cursor = conn.cursor()
    cursor.execute(adapt_sql("SELECT name, price, quantity FROM inventory WHERE id = ?", conn), (item_id,))
    row = cursor.fetchone()
    if row is None:
        print(f"No item found with ID {item_id}.")
        return
    # Determine new values
    new_name = name.strip() if name else row["name"]
    new_price = price if price is not None else row["price"]
    new_quantity = quantity if quantity is not None else row["quantity"]
    cursor.execute(
        adapt_sql(
            "UPDATE inventory SET name = ?, price = ?, quantity = ? WHERE id = ?",
            conn,
        ),
        (new_name, new_price, new_quantity, item_id),
    )
    conn.commit()
    print(f"Item {item_id} updated.")


def delete_item(conn: sqlite3.Connection, item_id: int) -> None:
    """Remove an item from the inventory. Associated sales remain in the history."""
    cursor = conn.cursor()
    cursor.execute(adapt_sql("SELECT name FROM inventory WHERE id = ?", conn), (item_id,))
    row = cursor.fetchone()
    if row is None:
        print(f"No item found with ID {item_id}.")
        return
    cursor.execute(adapt_sql("DELETE FROM inventory WHERE id = ?", conn), (item_id,))
    conn.commit()
    print(f"Item '{row['name']}' deleted from inventory.")


def prompt_for_float(prompt: str) -> float:
    """Prompt the user for a floating point number, repeating until valid."""
    while True:
        value = input(prompt).strip()
        try:
            return float(value)
        except ValueError:
            print("Please enter a valid number.")


def prompt_for_int(prompt: str) -> int:
    """Prompt the user for an integer, repeating until valid."""
    while True:
        value = input(prompt).strip()
        try:
            return int(value)
        except ValueError:
            print("Please enter a valid integer.")


def main_menu(conn: sqlite3.Connection) -> None:
    """Display the main menu and dispatch user selections."""
    while True:
        print("""
==== Local POS System ====
1. Add new item
2. View inventory
3. Update an item
4. Delete an item
5. Record a sale
6. View sales history
7. Exit
""")
        choice = input("Select an option (1-7): ").strip()
        if choice == "1":
            name = input("Enter item name: ").strip()
            if not name:
                print("Item name cannot be empty.")
                continue
            price = prompt_for_float("Enter item price: ")
            quantity = prompt_for_int("Enter starting quantity: ")
            add_item(conn, name, price, quantity)
        elif choice == "2":
            view_inventory(conn)
        elif choice == "3":
            item_id = prompt_for_int("Enter the ID of the item to update: ")
            print("Leave a field blank to keep the current value.")
            new_name = input("New name: ")
            price_input = input("New price: ")
            quantity_input = input("New quantity: ")
            new_price: Optional[float] = None
            new_quantity: Optional[int] = None
            if price_input.strip():
                try:
                    new_price = float(price_input)
                except ValueError:
                    print("Invalid price entered. Update aborted.")
                    continue
            if quantity_input.strip():
                try:
                    new_quantity = int(quantity_input)
                except ValueError:
                    print("Invalid quantity entered. Update aborted.")
                    continue
            update_item(conn, item_id, new_name if new_name.strip() else None, new_price, new_quantity)
        elif choice == "4":
            item_id = prompt_for_int("Enter the ID of the item to delete: ")
            delete_item(conn, item_id)
        elif choice == "5":
            view_inventory(conn)
            item_id = prompt_for_int("Enter the ID of the item to sell: ")
            quantity = prompt_for_int("Enter quantity sold: ")
            record_sale(conn, item_id, quantity)
        elif choice == "6":
            view_sales(conn)
        elif choice == "7":
            print("Exiting...")
            break
        else:
            print("Invalid option. Please choose a number between 1 and 7.")


if __name__ == "__main__":
    # On startup, connect to the database.  This will create tables if needed.
    conn = get_connection()
    try:
        main_menu(conn)
    finally:
        conn.close()