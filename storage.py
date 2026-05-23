from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists messages (
                    id integer primary key autoincrement,
                    phone text not null,
                    direction text not null,
                    body text not null,
                    created_at integer not null
                );

                create table if not exists memories (
                    id integer primary key autoincrement,
                    phone text not null,
                    fact text not null,
                    confidence real not null default 0.7,
                    created_at integer not null
                );

                create table if not exists opt_outs (
                    phone text primary key,
                    created_at integer not null
                );

                create table if not exists order_states (
                    phone text primary key,
                    state text not null,
                    product text,
                    category text,
                    distributor text,
                    distributor_cost real,
                    client_quote real,
                    availability text,
                    client_name text,
                    pickup_time text,
                    updated_at integer not null
                );

                create table if not exists distributor_requests (
                    id integer primary key autoincrement,
                    client_phone text not null,
                    distributor text not null,
                    product text not null,
                    status text not null,
                    created_at integer not null,
                    updated_at integer not null
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("pragma table_info(order_states)").fetchall()
            }
            if "category" not in columns:
                conn.execute("alter table order_states add column category text")

    def add_message(self, phone: str, direction: str, body: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "insert into messages(phone, direction, body, created_at) values (?, ?, ?, ?)",
                (phone, direction, body, int(time.time())),
            )

    def add_memory(self, phone: str, fact: str, confidence: float = 0.7) -> None:
        fact = " ".join(fact.split())
        if len(fact) < 12:
            return
        with self.connect() as conn:
            exists = conn.execute(
                "select 1 from memories where phone = ? and lower(fact) = lower(?)",
                (phone, fact),
            ).fetchone()
            if exists:
                return
            conn.execute(
                "insert into memories(phone, fact, confidence, created_at) values (?, ?, ?, ?)",
                (phone, fact[:500], confidence, int(time.time())),
            )

    def search_memories(self, phone: str, query_terms: Iterable[str], limit: int = 8) -> list[str]:
        terms = {term.lower() for term in query_terms if len(term) > 2}
        with self.connect() as conn:
            rows = conn.execute(
                "select fact from memories where phone = ? order by created_at desc limit 200",
                (phone,),
            ).fetchall()
        scored: list[tuple[int, str]] = []
        for row in rows:
            fact = row["fact"]
            haystack = fact.lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, fact))
        scored.sort(reverse=True, key=lambda item: item[0])
        return [fact for _, fact in scored[:limit]]

    def recent_messages(self, phone: str, limit: int = 10) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                select direction, body from messages
                where phone = ?
                order by created_at desc
                limit ?
                """,
                (phone, limit),
            ).fetchall()[::-1]

    def opt_out(self, phone: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "insert or replace into opt_outs(phone, created_at) values (?, ?)",
                (phone, int(time.time())),
            )

    def is_opted_out(self, phone: str) -> bool:
        with self.connect() as conn:
            return bool(conn.execute("select 1 from opt_outs where phone = ?", (phone,)).fetchone())

    def forget(self, phone: str) -> None:
        with self.connect() as conn:
            conn.execute("delete from memories where phone = ?", (phone,))
            conn.execute("delete from messages where phone = ?", (phone,))
            conn.execute("delete from opt_outs where phone = ?", (phone,))
            conn.execute("delete from order_states where phone = ?", (phone,))

    def get_order_state(self, phone: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("select * from order_states where phone = ?", (phone,)).fetchone()
        return dict(row) if row else None

    def set_order_state(self, phone: str, **values) -> None:
        current = self.get_order_state(phone) or {}
        merged = {
            "phone": phone,
            "state": values.get("state", current.get("state", "GREETING")),
            "product": values.get("product", current.get("product")),
            "category": values.get("category", current.get("category")),
            "distributor": values.get("distributor", current.get("distributor")),
            "distributor_cost": values.get("distributor_cost", current.get("distributor_cost")),
            "client_quote": values.get("client_quote", current.get("client_quote")),
            "availability": values.get("availability", current.get("availability")),
            "client_name": values.get("client_name", current.get("client_name")),
            "pickup_time": values.get("pickup_time", current.get("pickup_time")),
            "updated_at": int(time.time()),
        }
        with self.connect() as conn:
            conn.execute(
                """
                insert into order_states(
                    phone, state, product, category, distributor, distributor_cost, client_quote,
                    availability, client_name, pickup_time, updated_at
                ) values (
                    :phone, :state, :product, :category, :distributor, :distributor_cost, :client_quote,
                    :availability, :client_name, :pickup_time, :updated_at
                )
                on conflict(phone) do update set
                    state = excluded.state,
                    product = excluded.product,
                    category = excluded.category,
                    distributor = excluded.distributor,
                    distributor_cost = excluded.distributor_cost,
                    client_quote = excluded.client_quote,
                    availability = excluded.availability,
                    client_name = excluded.client_name,
                    pickup_time = excluded.pickup_time,
                    updated_at = excluded.updated_at
                """,
                merged,
            )

    def add_distributor_request(self, client_phone: str, distributor: str, product: str) -> None:
        now = int(time.time())
        with self.connect() as conn:
            conn.execute(
                """
                insert into distributor_requests(client_phone, distributor, product, status, created_at, updated_at)
                values (?, ?, ?, 'OPEN', ?, ?)
                """,
                (client_phone, distributor, product, now, now),
            )

    def latest_open_request(self, distributor: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                select * from distributor_requests
                where distributor = ? and status = 'OPEN'
                order by created_at desc
                limit 1
                """,
                (distributor,),
            ).fetchone()
        return dict(row) if row else None

    def close_distributor_request(self, request_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "update distributor_requests set status = 'CLOSED', updated_at = ? where id = ?",
                (int(time.time()), request_id),
            )
