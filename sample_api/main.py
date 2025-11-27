"""A small, self-contained e-commerce API: the "internal API" agentgate fronts.

Deliberately simple (in-memory store, no auth of its own) -- agentgate is
what supplies auth, policy, and budget control in front of it. This is the
downstream service the demo agent's tool calls ultimately land on.
"""
from __future__ import annotations

import itertools
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

app = FastAPI(
    title="Acme E-Commerce API",
    description="A minimal internal e-commerce API used to demo agentgate.",
    version="1.0.0",
)

_order_id_seq = itertools.count(1)


class Product(BaseModel):
    id: str
    name: str
    category: str
    price_cents: int
    stock: int


class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)


class CreateOrderBody(BaseModel):
    items: list[OrderItem]
    customer_email: str


class Order(BaseModel):
    id: str
    status: Literal["pending", "confirmed", "cancelled"]
    items: list[OrderItem]
    customer_email: str
    total_cents: int


_PRODUCTS: dict[str, Product] = {
    "sku-001": Product(
        id="sku-001", name="Wireless Mouse", category="electronics", price_cents=2499, stock=50
    ),
    "sku-002": Product(
        id="sku-002", name="Mechanical Keyboard", category="electronics", price_cents=8999, stock=20
    ),
    "sku-003": Product(
        id="sku-003", name="USB-C Hub", category="electronics", price_cents=3499, stock=35
    ),
    "sku-004": Product(
        id="sku-004", name="Standing Desk Mat", category="furniture", price_cents=4999, stock=15
    ),
}
_ORDERS: dict[str, Order] = {}


@app.get("/ping", operation_id="ping", response_class=PlainTextResponse)
def ping() -> str:
    """A trivial liveness check that deliberately returns plain text, not JSON."""
    return "pong"


@app.get("/products", operation_id="list_products", response_model=list[Product])
def list_products(category: str | None = None) -> list[Product]:
    """List all products, optionally filtered by category."""
    values = list(_PRODUCTS.values())
    if category:
        values = [p for p in values if p.category == category]
    return values


@app.get("/products/{product_id}", operation_id="get_product", response_model=Product)
def get_product(product_id: str) -> Product:
    """Fetch a single product by its SKU."""
    product = _PRODUCTS.get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"unknown product '{product_id}'")
    return product


@app.post("/orders", operation_id="create_order", response_model=Order, status_code=201)
def create_order(body: CreateOrderBody) -> Order:
    """Place a new order for one or more products, decrementing stock."""
    total = 0
    for item in body.items:
        product = _PRODUCTS.get(item.product_id)
        if product is None:
            raise HTTPException(status_code=404, detail=f"unknown product '{item.product_id}'")
        if product.stock < item.quantity:
            raise HTTPException(
                status_code=409, detail=f"insufficient stock for '{item.product_id}'"
            )
        total += product.price_cents * item.quantity

    for item in body.items:
        _PRODUCTS[item.product_id].stock -= item.quantity

    order_id = f"ord-{next(_order_id_seq):06d}"
    order = Order(
        id=order_id,
        status="confirmed",
        items=body.items,
        customer_email=body.customer_email,
        total_cents=total,
    )
    _ORDERS[order_id] = order
    return order


@app.get("/orders/{order_id}", operation_id="get_order", response_model=Order)
def get_order(order_id: str) -> Order:
    """Fetch a previously-placed order by id."""
    order = _ORDERS.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"unknown order '{order_id}'")
    return order


@app.post("/orders/{order_id}/cancel", operation_id="cancel_order", response_model=Order)
def cancel_order(order_id: str) -> Order:
    """Cancel a pending or confirmed order and restock its items."""
    order = _ORDERS.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"unknown order '{order_id}'")
    if order.status == "cancelled":
        raise HTTPException(status_code=409, detail="order already cancelled")
    for item in order.items:
        if item.product_id in _PRODUCTS:
            _PRODUCTS[item.product_id].stock += item.quantity
    order.status = "cancelled"
    return order
