"""SQLite ledger for forward paper trading (~/.tradebot/tradebot.db)."""
import pathlib
import sqlite3

DB = pathlib.Path.home() / ".tradebot" / "tradebot.db"


def get_conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB)


def init_db():
    c = get_conn(); cur = c.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS paper_book(
        book_id INTEGER PRIMARY KEY AUTOINCREMENT, product TEXT, strategy TEXT, params TEXT,
        position INTEGER DEFAULT 0, units REAL DEFAULT 0, cash REAL, notional REAL,
        last_date TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, active INTEGER DEFAULT 1)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS paper_marks(
        mark_id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER, date TEXT, price REAL,
        position INTEGER, equity REAL)""")
    c.commit(); c.close()
