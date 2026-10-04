
LOOKUP_ORDER_TOOL = {
    "type": "function",
    "name": "lookup_order",
    "description": "Look up an order by its order ID and return its current status and details.",
    "parameters": {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "The ID of the order to look up.",
            }
        },
        "required": ["order_id"],
        "additionalProperties": False,
    },
}

LOOKUP_CUSTOMER_TOOL = {
    "type": "function",
    "name": "lookup_customer",
    "description": "Look up a customer by their customer ID and return their details.",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {
                "type": "string",
                "description": "The ID of the customer to look up.",
            }
        },
        "required": ["customer_id"],
        "additionalProperties": False,
    },
}
