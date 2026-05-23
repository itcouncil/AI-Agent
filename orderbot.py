from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from brain import search_catalog
from config import Settings
from storage import Store


DISTRIBUTOR_ROUTES = {
    "phones": ["PHONE_DISTRIBUTOR_1", "PHONE_DISTRIBUTOR_2", "PHONE_DISTRIBUTOR_3", "PHONE_DISTRIBUTOR_4"],
    "accessories": [
        "ACCESSORY_DISTRIBUTOR_1",
        "ACCESSORY_DISTRIBUTOR_2",
        "ACCESSORY_DISTRIBUTOR_3",
        "ACCESSORY_DISTRIBUTOR_4",
        "ACCESSORY_DISTRIBUTOR_5",
        "ACCESSORY_DISTRIBUTOR_6",
        "ACCESSORY_DISTRIBUTOR_7",
    ],
    "laptops": [
        "LAPTOP_DISTRIBUTOR_1",
        "LAPTOP_DISTRIBUTOR_2",
        "LAPTOP_DISTRIBUTOR_3",
        "LAPTOP_DISTRIBUTOR_4",
        "LAPTOP_DISTRIBUTOR_5",
        "LAPTOP_DISTRIBUTOR_6",
        "LAPTOP_DISTRIBUTOR_7",
        "LAPTOP_DISTRIBUTOR_8",
        "LAPTOP_DISTRIBUTOR_9",
        "LAPTOP_DISTRIBUTOR_10",
    ],
    "oraimo_accessories": ["ORAIMO_SUPPLIER"],
    "amaya_accessories": ["AMAYA_SUPPLIER"],
    "infinix_accessories": ["INFINIX_ACCESSORY_SUPPLIER"],
    "logitech": ["LOGITECH_SUPPLIER"],
    "general": ["GENERAL_DISTRIBUTOR"],
}
DISTRIBUTORS = {name: category for category, names in DISTRIBUTOR_ROUTES.items() for name in names}


@dataclass
class BotResult:
    client_reply: str | None = None
    distributor_message: str | None = None
    distributor: str | None = None
    attendant_message: str | None = None
    state: str | None = None


class OrderBot:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.routes = load_distributor_routes(settings.catalog_path.parent / "distributors.json")

    def handle_client(self, phone: str, text: str) -> BotResult:
        normalized = text.lower().strip()
        current = self.store.get_order_state(phone) or {"state": "GREETING"}

        if any(word in normalized for word in ("angry", "confused", "upset", "complaint")):
            self.store.set_order_state(phone, state="ESCALATED")
            return BotResult(
                client_reply="Let me have the shop owner call you personally.",
                state="ESCALATED",
            )

        if normalized in {"stop", "cancel"}:
            self.store.set_order_state(phone, state="CANCELLED")
            return BotResult(client_reply="No problem, I have cancelled this request.", state="CANCELLED")

        state = current["state"]
        if state in {"AWAITING_CONFIRMATION", "QUOTING"} and is_yes(normalized):
            self.store.set_order_state(phone, state="AWAITING_PAYMENT")
            return BotResult(
                client_reply=(
                    "Great, I have reserved it for you. Please send payment to the shop till/paybill "
                    "and share the receipt or reference here so I can verify it."
                ),
                state="AWAITING_PAYMENT",
            )

        if state == "AWAITING_PAYMENT":
            self.store.set_order_state(phone, state="VERIFYING_PAYMENT")
            return BotResult(
                client_reply="Thanks. I am checking the payment now and will confirm pickup once verified.",
                state="VERIFYING_PAYMENT",
            )

        product = identify_product(text)
        if not product:
            self.store.set_order_state(phone, state="IDENTIFYING_PRODUCT")
            return BotResult(
                client_reply="Hi, happy to help. Which exact product, model, or size do you want?",
                state="IDENTIFYING_PRODUCT",
            )

        catalog_hits = search_catalog(self.settings.catalog_path, product.lower().split())
        if not catalog_hits:
            self.store.set_order_state(phone, state="IDENTIFYING_PRODUCT", product=product)
            return BotResult(
                client_reply=(
                    f"Let me confirm whether we can source {product}. "
                    "Please share the exact model or a photo if you have one."
                ),
                state="IDENTIFYING_PRODUCT",
            )

        category = detect_category(product)
        distributor = self.first_distributor(category)
        self.store.set_order_state(
            phone,
            state="CHECKING_PRICE",
            product=product,
            category=category,
            distributor=distributor,
            availability=None,
            distributor_cost=None,
            client_quote=None,
        )
        self.store.add_distributor_request(phone, distributor, product)
        outbound = stock_check_message(product)
        return BotResult(
            client_reply="Let me check current pricing with our supplier. I'll get back to you in a moment.",
            distributor_message=outbound,
            distributor=distributor,
            state="CHECKING_PRICE",
        )

    def handle_distributor(self, distributor: str, text: str) -> BotResult:
        request = self.store.latest_open_request(distributor)
        if not request:
            return BotResult(distributor_message="No open stock-check request for this distributor.")

        if is_out_of_stock(text):
            self.store.close_distributor_request(request["id"])
            current = self.store.get_order_state(request["client_phone"]) or {}
            category = current.get("category") or detect_category(request["product"])
            next_distributor = self.next_distributor(category, distributor)
            if next_distributor:
                self.store.set_order_state(
                    request["client_phone"],
                    state="CHECKING_PRICE",
                    product=request["product"],
                    category=category,
                    distributor=next_distributor,
                    availability="checking next supplier",
                )
                self.store.add_distributor_request(request["client_phone"], next_distributor, request["product"])
                return BotResult(
                    distributor=next_distributor,
                    distributor_message=stock_check_message(request["product"]),
                    state="CHECKING_PRICE",
                )

            self.store.set_order_state(
                request["client_phone"],
                state="IDENTIFYING_PRODUCT",
                product=request["product"],
                category=category,
                availability="not available",
            )
            return BotResult(
                client_reply=(
                    f"I checked our suppliers for {request['product']}, but it is not available right now. "
                    "Would you like a close alternative?"
                ),
                state="IDENTIFYING_PRODUCT",
            )

        parsed = parse_distributor_price(text)
        if not parsed:
            return BotResult(
                distributor_message=(
                    "Please reply with current cost and availability, for example: "
                    "'cost 9000 available 3 units'."
                )
            )

        cost, availability = parsed
        quote = round_to_nearest_50(cost * 1.25)
        self.store.close_distributor_request(request["id"])
        self.store.set_order_state(
            request["client_phone"],
            state="AWAITING_CONFIRMATION",
            product=request["product"],
            category=(self.store.get_order_state(request["client_phone"]) or {}).get("category"),
            distributor=distributor,
            distributor_cost=cost,
            client_quote=quote,
            availability=availability,
        )
        return BotResult(
            client_reply=(
                f"{request['product']} is available at {money(quote)} final price, inclusive of all costs. "
                f"Availability: {availability}. Pickup location: Moi Avenue shop. "
                "Would you like me to place the order for you?"
            ),
            state="AWAITING_CONFIRMATION",
        )

    def first_distributor(self, category: str) -> str:
        return self.routes.get(category, self.routes["general"])[0]

    def next_distributor(self, category: str, current_distributor: str) -> str | None:
        route = self.routes.get(category, self.routes["general"])
        if current_distributor not in route:
            return route[0] if route else None
        next_index = route.index(current_distributor) + 1
        return route[next_index] if next_index < len(route) else None

    def verify_payment(self, phone: str, amount: float, received: bool = True) -> BotResult:
        current = self.store.get_order_state(phone)
        if not current:
            return BotResult(client_reply="I could not find an active order for this customer.")
        expected = float(current.get("client_quote") or 0)
        if not received or amount < expected:
            return BotResult(
                client_reply=f"I have not confirmed full payment yet. Expected amount is {money(expected)}."
            )
        self.store.set_order_state(phone, state="COORDINATING_PICKUP")
        return BotResult(
            client_reply="Payment confirmed. Please share your name and preferred pickup time.",
            state="COORDINATING_PICKUP",
        )

    def coordinate_pickup(self, phone: str, client_name: str, pickup_time: str) -> BotResult:
        current = self.store.get_order_state(phone)
        if not current:
            return BotResult(client_reply="I could not find an active order for this customer.")
        self.store.set_order_state(
            phone,
            state="COMPLETED",
            client_name=client_name,
            pickup_time=pickup_time,
        )
        attendant = (
            f"ORDER READY: {client_name}, {current.get('product')}, "
            f"paid {money(float(current.get('client_quote') or 0))}, pickup {pickup_time}."
        )
        return BotResult(
            client_reply="You're all set. Your order will be ready at the Moi Avenue shop for pickup.",
            attendant_message=attendant,
            state="COMPLETED",
        )


def identify_product(text: str) -> str:
    cleaned = re.sub(r"\b(how much|price|cost|need|want|for|do you have|i need|i want)\b", " ", text, flags=re.I)
    cleaned = " ".join(cleaned.replace("?", " ").split())
    return cleaned[:120].strip()


def detect_category(product: str) -> str:
    lower = product.lower()
    accessory_terms = (
        "accessory", "accessories", "buds", "earbuds", "earphone", "headphone", "charger",
        "cable", "case", "cover", "screen protector", "power bank", "speaker", "mouse", "keyboard",
    )
    if "logitech" in lower:
        return "logitech"
    if "oraimo" in lower:
        return "oraimo_accessories"
    if "amaya" in lower:
        return "amaya_accessories"
    if "infinix" in lower and any(term in lower for term in accessory_terms):
        return "infinix_accessories"
    if any(word in lower for word in ("laptop", "notebook", "macbook", "thinkpad", "elitebook", "latitude")):
        return "laptops"
    if any(word in lower for word in ("phone", "iphone", "samsung", "infinix", "tecno", "oppo", "redmi", "xiaomi")):
        return "phones"
    if any(term in lower for term in accessory_terms):
        return "accessories"
    return "general"


def load_distributor_routes(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return DISTRIBUTOR_ROUTES
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    routes = {key: [str(name) for name in value if str(name).strip()] for key, value in loaded.items()}
    routes.setdefault("general", ["GENERAL_DISTRIBUTOR"])
    return routes


def stock_check_message(product: str) -> str:
    return f"URGENT STOCK CHECK: {product}. Need current price and availability. Client waiting."


def is_out_of_stock(text: str) -> bool:
    return bool(re.search(r"\b(out of stock|out|unavailable|no stock|not available|sold out)\b", text, flags=re.I))


def parse_distributor_price(text: str) -> tuple[float, str] | None:
    amount_match = re.search(r"(\$|kes|ksh)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text, flags=re.I)
    if not amount_match:
        return None
    cost = float(amount_match.group(2).replace(",", ""))
    availability = "confirmed"
    if is_out_of_stock(text):
        availability = "not available"
    else:
        stock_match = re.search(r"((?:available|stock|units?).{0,40})", text, flags=re.I)
        if stock_match:
            availability = stock_match.group(1).strip()
    return cost, availability


def round_to_nearest_50(value: float) -> int:
    increment = 5 if value < 1000 else 50
    return int(math.ceil(value / increment) * increment)


def money(value: float) -> str:
    if value >= 1000:
        return f"KES {value:,.0f}"
    return f"${value:,.0f}"


def is_yes(text: str) -> bool:
    return text in {"yes", "y", "ok", "okay", "confirm", "proceed", "place order", "go ahead"}
