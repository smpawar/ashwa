"""Create the application schema and seed orders + knowledge base.

Run once (re-running is safe; it rebuilds the tables):
    .venv/bin/python seed.py
"""

import random
from datetime import datetime, timedelta, timezone

from common import EMBED_DIM, EMBED_MODEL, conn, content_hash, embed, langfuse

random.seed(7)
NOW = datetime.now(timezone.utc)

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
DROP TABLE IF EXISTS refunds, tickets, orders, kb_docs;
CREATE TABLE orders (
    order_id     text PRIMARY KEY,
    customer_id  text NOT NULL,
    product      text NOT NULL,
    amount       numeric(10,2) NOT NULL,
    status       text NOT NULL,          -- processing | shipped | delivered | cancelled
    carrier      text,
    tracking_no  text,
    ordered_at   timestamptz NOT NULL,
    delivered_at timestamptz
);
CREATE TABLE refunds (
    refund_id  text PRIMARY KEY,
    order_id   text NOT NULL REFERENCES orders,
    amount     numeric(10,2) NOT NULL,
    reason     text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tickets (
    ticket_id  serial PRIMARY KEY,
    reason     text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE kb_docs (
    doc_id       serial PRIMARY KEY,
    title        text NOT NULL,
    body         text NOT NULL,
    content_hash text NOT NULL,
    embed_model  text NOT NULL,
    embedding    vector({EMBED_DIM}) NOT NULL
);
"""

PRODUCTS = [("X200 Blender", 89.00), ("AirChef Fryer", 129.00), ("BrewMaster Kettle", 59.00),
            ("Q5 Stand Mixer", 249.00), ("FrostLine Mini Fridge", 199.00), ("SteamPro Iron", 45.00)]

# Orders the workload scenarios refer to: (status, days since shipped/delivered, product).
# Products are explicit so they match what the customer says in workload.py.
SCENARIO_ORDERS = {
    "100101": ("shipped", 3, "Q5 Stand Mixer"),           # in transit
    "100102": ("shipped", 9, "FrostLine Mini Fridge"),    # stuck in transit
    "100103": ("delivered", 5, "X200 Blender"),           # "arrived cracked", refund-eligible
    "100104": ("delivered", 12, "BrewMaster Kettle"),     # change of mind, refund-eligible
    "100105": ("delivered", 45, "AirChef Fryer"),         # outside 30-day window
    "100106": ("processing", 1, "BrewMaster Kettle"),
    "100107": ("delivered", 2, "Q5 Stand Mixer"),         # wrong colour, refund-eligible
    "100108": ("shipped", 6, "SteamPro Iron"),
    "100109": ("delivered", 20, "SteamPro Iron"),         # refund-eligible
    "100110": ("processing", 4, "X200 Blender"),          # not delivered: refund must be refused
    "100111": ("delivered", 8, "AirChef Fryer"),          # repeat complaint, escalate
    "100112": ("shipped", 2, "FrostLine Mini Fridge"),
}
PRICES = dict(PRODUCTS)

KB = [
    ("Refund policy", "Items can be refunded within 30 days of delivery. Refunds go back to the original payment method and take 5-7 business days to appear. Items damaged on arrival are always refundable within 30 days."),
    ("Refunds outside 30 days", "After 30 days from delivery, orders are not refundable, but defective items may be covered by the manufacturer warranty. Offer a warranty claim instead of a refund."),
    ("Damaged on arrival", "If an item arrives damaged, apologise, confirm the order, and issue a refund or replacement. No need to return the damaged item for orders under $150."),
    ("Shipping times", "Standard shipping takes 3-5 business days after an order ships. Expedited shipping takes 1-2 business days. Orders usually ship within 2 business days of being placed."),
    ("Delayed shipments", "If tracking has not updated for more than 5 days, the package may be delayed at a carrier hub. Offer to open a carrier trace; if it is not found within 3 business days, reship or refund."),
    ("Tracking links", "Tracking links are sent by email when the order ships. Agents can share the carrier and tracking number from the order record."),
    ("Order cancellation", "Orders can be cancelled free of charge while they are still processing. Once shipped, the customer must wait for delivery and request a return."),
    ("Warranty", "All blenders, fryers, mixers and fridges carry a 2-year limited warranty covering manufacturing defects. Kettles and irons carry a 1-year warranty."),
    ("X200 Blender specs", "The X200 Blender is dual voltage (110-240V), has a 1.5 litre jar, and the jar and lid are dishwasher safe. The motor base is not."),
    ("AirChef Fryer specs", "The AirChef Fryer has a 5.5 litre basket, non-stick coating, and runs on 120V only. The basket is dishwasher safe."),
    ("BrewMaster Kettle specs", "The BrewMaster Kettle holds 1.7 litres, has 5 temperature presets, and automatically shuts off when boiled dry."),
    ("Q5 Stand Mixer specs", "The Q5 Stand Mixer has a 5 quart bowl, 10 speeds, and comes with a whisk, dough hook and paddle. Attachments are dishwasher safe."),
    ("FrostLine Mini Fridge specs", "The FrostLine Mini Fridge has 3.1 cubic feet of storage, a reversible door, and draws 80W. It needs 2 inches of clearance at the back."),
    ("SteamPro Iron specs", "The SteamPro Iron has a ceramic soleplate, vertical steam, and a 300ml water tank. Use distilled water to avoid scale."),
    ("Escalation policy", "Escalate to a human agent when a customer asks for a manager, threatens a chargeback, or has contacted support three or more times about the same order."),
    ("Compensation guidelines", "For repeated delivery problems, agents may offer a $10 store credit. Larger compensation requires a human agent."),
    ("Returns process", "Returns are free for defective items. For change-of-mind returns within 30 days, a $7.95 return shipping fee is deducted from the refund."),
    ("Payment methods", "We accept major credit cards, PayPal and store gift cards. Refunds to gift cards are issued as store credit."),
    ("International orders", "We ship to the US and Canada only. Voltage-specific products such as the AirChef Fryer are not suitable for 220V countries."),
    ("Price match", "We match the price of major US retailers within 14 days of purchase. A price-match request needs a link to the competitor listing."),
    ("Account and privacy", "Agents must never ask for full card numbers or passwords. Verify identity using the order number and email address only."),
    ("Replacement parts", "Replacement jars, baskets and lids can be ordered through support. Parts ship within 3 business days and are covered by the product warranty."),
    ("Holiday hours", "Support is available 8am-8pm Eastern, seven days a week, except on major US holidays."),
    ("Gift orders", "Gift receipts hide prices. Refunds for gift orders go to the original purchaser unless the recipient requests store credit."),
]


def reset_phase_state():
    """Put the scenario orders back in a known state. Called at the start of
    every phase so v1 and v2 face identical data: no refunds or tickets left
    over from an earlier phase, and dates re-based on 'now' so refund
    eligibility does not drift between runs on different days."""
    now = datetime.now(timezone.utc)
    with conn().cursor() as cur:
        cur.execute("TRUNCATE refunds, tickets RESTART IDENTITY")
        for oid, (status, days, _product) in SCENARIO_ORDERS.items():
            ordered = now - timedelta(days=days + 4)
            delivered = now - timedelta(days=days) if status == "delivered" else None
            cur.execute("UPDATE orders SET status = %s, ordered_at = %s, delivered_at = %s WHERE order_id = %s",
                        (status, ordered, delivered, oid))


def seed_orders() -> int:
    """Create the schema and orders. No API calls."""
    with conn().cursor() as cur:
        cur.execute(SCHEMA)

    rows = []
    for i in range(300):
        oid = str(100500 + i)
        product, price = random.choice(PRODUCTS)
        status = random.choice(["processing", "shipped", "delivered", "delivered", "delivered", "cancelled"])
        ordered = NOW - timedelta(days=random.randint(1, 90))
        delivered = ordered + timedelta(days=random.randint(2, 6)) if status == "delivered" else None
        rows.append((oid, f"C{random.randint(1000, 1200)}", product, price, status, "UPS", f"1Z{random.randint(10**9, 10**10)}", ordered, delivered))
    for oid, (status, _days, product) in SCENARIO_ORDERS.items():
        rows.append((oid, f"C{oid[-3:]}", product, PRICES[product], status, "UPS", f"1Z{oid}88", NOW, None))
    with conn().cursor() as cur:
        cur.executemany("INSERT INTO orders VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    reset_phase_state()
    return len(rows)


def seed_kb():
    """Embed and load the knowledge base (one embedding call per article)."""
    with langfuse.start_as_current_span(name="seed_knowledge_base"):
        for title, body in KB:
            text = f"{title}. {body}"
            vec = embed(text, task="RETRIEVAL_DOCUMENT")
            with conn().cursor() as cur:
                cur.execute("INSERT INTO kb_docs (title, body, content_hash, embed_model, embedding) VALUES (%s,%s,%s,%s,%s)",
                            (title, body, content_hash(text), EMBED_MODEL, vec))
    langfuse.flush()


def main():
    n = seed_orders()
    seed_kb()
    with conn().cursor() as cur:
        cur.execute("SELECT pg_stat_statements_reset()")
    print(f"Seeded {n} orders and {len(KB)} knowledge-base articles.")


if __name__ == "__main__":
    main()
