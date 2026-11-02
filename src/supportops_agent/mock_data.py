"""In-memory fixture data backing the deterministic lookup tools."""

from datetime import date

from supportops_agent.models import OrderRecord, OrderStatus, SupportCase


CUSTOMERS: dict[str, dict[str, str]] = {
    "customer_001": {
        "customer_id": "customer_001",
        "name": "John",
        "email": "john@example.com",
    },
    "customer_002": {
        "customer_id": "customer_002",
        "name": "Alice",
        "email": "alice@example.com",
    },
    "customer_003": {
        "customer_id": "customer_003",
        "name": "Mia Chen",
        "email": "mia.chen@example.com",
    },
    "customer_004": {
        "customer_id": "customer_004",
        "name": "Diego Alvarez",
        "email": "diego.alvarez@example.com",
    },
    "customer_005": {
        "customer_id": "customer_005",
        "name": "Yuki Tanaka",
        "email": "yuki.tanaka@example.com",
    },
}

ORDERS: dict[str, OrderRecord] = {
    "order_001": OrderRecord(
        order_id="order_001",
        customer_id="customer_001",
        status=OrderStatus.DELIVERED,
        amount_cents=12999,
        currency="EUR",
        created_at=date(2026, 7, 20),
    ),
    "order_002": OrderRecord(
        order_id="order_002",
        customer_id="customer_002",
        status=OrderStatus.SHIPPED,
        amount_cents=4999,
        currency="EUR",
        created_at=date(2026, 8, 5),
    ),
    "order_003": OrderRecord(
        order_id="order_003",
        customer_id="customer_003",
        status=OrderStatus.PAID,
        amount_cents=8550,
        currency="USD",
        created_at=date(2026, 9, 1),
    ),
    "order_004": OrderRecord(
        order_id="order_004",
        customer_id="customer_004",
        status=OrderStatus.CANCELLED,
        amount_cents=2300,
        currency="GBP",
        created_at=date(2026, 9, 10),
    ),
    "order_005": OrderRecord(
        order_id="order_005",
        customer_id="customer_005",
        status=OrderStatus.REFUNDED,
        amount_cents=15999,
        currency="USD",
        created_at=date(2026, 9, 15),
    ),
    "order_006": OrderRecord(
        order_id="order_006",
        customer_id="customer_002",
        status=OrderStatus.PAID,
        amount_cents=3499,
        currency="EUR",
        created_at=date(2026, 9, 22),
    ),
    "order_007": OrderRecord(
        order_id="order_007",
        customer_id="customer_003",
        status=OrderStatus.SHIPPED,
        amount_cents=6700,
        currency="GBP",
        created_at=date(2026, 9, 28),
    ),
    "order_008": OrderRecord(
        order_id="order_008",
        customer_id="customer_001",
        status=OrderStatus.DELIVERED,
        amount_cents=5400,
        currency="EUR",
        created_at=date(2026, 9, 20),
    ),
}

# Fixed "today" for business rules, so results never depend on the wall clock.
BUSINESS_DATE = date(2026, 10, 1)

# Shipment data for orders that have left the warehouse.
TRACKING: dict[str, dict[str, str]] = {
    "order_001": {
        "carrier": "DHL",
        "tracking_number": "DHL-0001",
        "shipment_status": "delivered",
        "last_update": "2026-07-25",
    },
    "order_002": {
        "carrier": "UPS",
        "tracking_number": "UPS-0002",
        "shipment_status": "in_transit",
        "last_update": "2026-08-07",
    },
    "order_007": {
        "carrier": "DPD",
        "tracking_number": "DPD-0007",
        "shipment_status": "in_transit",
        "last_update": "2026-09-30",
    },
    "order_008": {
        "carrier": "DHL",
        "tracking_number": "DHL-0008",
        "shipment_status": "delivered",
        "last_update": "2026-09-24",
    },
}

# In-memory support case storage, keyed by case_id.
CASES: dict[str, SupportCase] = {}
