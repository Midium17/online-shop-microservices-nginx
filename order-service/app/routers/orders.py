import datetime
import io
import os
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_optional_current_user,
    require_permission,
)
from app.permissions import Permission

PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL", "http://127.0.0.1:8002")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:8001")

router = APIRouter(
    prefix="/orders",
    tags=["Orders"],
)


def _sync_user_profile(authorization: Optional[str], order_data: schemas.OrderCreate) -> None:
    if not authorization:
        return
    payload = {
        k: v
        for k, v in {
            "first_name": order_data.first_name,
            "last_name": order_data.last_name,
            "phone": order_data.phone,
            "address": order_data.address,
            "city": order_data.city,
            "postal_code": order_data.postal_code,
            "country": order_data.country,
        }.items()
        if v is not None
    }
    if not payload:
        return
    try:
        with httpx.Client(timeout=3.0) as client:
            client.patch(
                f"{AUTH_SERVICE_URL}/auth/me",
                json=payload,
                headers={"Authorization": authorization},
            )
    except httpx.RequestError:
        # Non-blocking: order creation should not fail if profile sync times out
        pass


def _rollback_stock(reserved_items: list[tuple[int, int]]) -> None:
    if not reserved_items:
        return
    with httpx.Client(timeout=5.0) as client:
        for prod_id, qty in reserved_items:
            try:
                client.patch(
                    f"{PRODUCT_SERVICE_URL}/products/{prod_id}/stock",
                    params={"quantity_delta": qty},
                )
            except httpx.RequestError:
                pass


@router.post("/", response_model=schemas.OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    order_data: schemas.OrderCreate,
    db: Session = Depends(get_db),
    current_user: Optional[CurrentUser] = Depends(get_optional_current_user),
    authorization: Optional[str] = Header(default=None),
):
    if not order_data.items:
        raise HTTPException(status_code=400, detail="Cannot place an order with an empty cart.")

    subtotal = 0.0
    order_items_to_create: list[models.OrderItemModel] = []
    reserved_stock: list[tuple[int, int]] = []

    try:
        with httpx.Client(timeout=5.0) as client:
            for item in order_data.items:
                try:
                    response = client.get(f"{PRODUCT_SERVICE_URL}/products/{item.product_id}")
                except httpx.RequestError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Product service is unreachable: {str(exc)}",
                    )

                if response.status_code == 404:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Product with ID {item.product_id} not found.",
                    )
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=502,
                        detail="Failed to communicate with product service.",
                    )

                product = response.json()
                if product.get("stock", 0) < item.quantity:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Insufficient stock for product: {product.get('title', item.product_id)}",
                    )

                try:
                    stock_resp = client.patch(
                        f"{PRODUCT_SERVICE_URL}/products/{item.product_id}/stock",
                        params={"quantity_delta": -item.quantity},
                    )
                except httpx.RequestError as exc:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Failed to deduct stock in product service: {str(exc)}",
                    )

                if stock_resp.status_code != 200:
                    detail = (
                        stock_resp.json().get("detail")
                        if stock_resp.headers.get("content-type", "").startswith("application/json")
                        else "Failed to update stock in product service."
                    )
                    raise HTTPException(
                        status_code=stock_resp.status_code if stock_resp.status_code in (400, 404) else 502,
                        detail=detail,
                    )

                reserved_stock.append((item.product_id, item.quantity))

                unit_price = float(product["price"])
                subtotal += unit_price * item.quantity
                order_items_to_create.append(
                    models.OrderItemModel(
                        product_id=product["id"],
                        product_title=product["title"],
                        quantity=item.quantity,
                        price=unit_price,
                    )
                )

        new_order = models.OrderModel(
            user_id=current_user.id if current_user else None,
            customer_email=order_data.customer_email or (current_user.email if current_user else None),
            first_name=order_data.first_name,
            last_name=order_data.last_name,
            phone=order_data.phone,
            address=order_data.address,
            city=order_data.city,
            postal_code=order_data.postal_code,
            country=order_data.country,
            total_amount=subtotal + 10.00,  # Including delivery or shipping fee
            status="Pending",
        )
        db.add(new_order)
        db.flush()

        for order_item in order_items_to_create:
            order_item.order_id = new_order.id
            db.add(order_item)

        db.commit()
        db.refresh(new_order)

        if current_user:
            _sync_user_profile(authorization, order_data)

        return new_order

    except Exception:
        db.rollback()
        _rollback_stock(reserved_stock)
        raise


@router.get("/", response_model=List[schemas.OrderResponse])
def get_orders(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.ORDERS_READ)),
):
    """Requires 'orders:read' permission (Admin & Order Manager)."""
    return db.query(models.OrderModel).all()


@router.patch("/{order_id}/status", response_model=schemas.OrderResponse)
def update_order_status(
    order_id: int,
    status_update: schemas.OrderStatusUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.ORDERS_UPDATE_STATUS)),
):
    """Requires 'orders:update_status' permission (Admin & Order Manager)."""
    order = db.query(models.OrderModel).filter(models.OrderModel.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    order.status = status_update.status
    db.commit()
    db.refresh(order)
    return order


@router.get("/my-orders", response_model=List[schemas.OrderResponse])
def get_my_orders(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve all orders placed by the currently logged-in user."""
    try:
        query = db.query(models.OrderModel)
        if current_user.id is not None:
            query = query.filter(
                (models.OrderModel.user_id == current_user.id)
                | (models.OrderModel.customer_email == current_user.email)
            )
        else:
            query = query.filter(models.OrderModel.customer_email == current_user.email)

        order_col = (
            models.OrderModel.created_at.desc()
            if hasattr(models.OrderModel, "created_at")
            else models.OrderModel.id.desc()
        )
        return query.order_by(order_col).all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {str(exc)}")


@router.patch("/{order_id}/cancel", response_model=schemas.OrderResponse)
def cancel_my_order(
    order_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Allow a logged-in customer to cancel their own pending order and restore stock."""
    order = db.query(models.OrderModel).filter(models.OrderModel.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    is_owner = (
        (current_user.id is not None and order.user_id == current_user.id)
        or (order.customer_email and order.customer_email.lower() == current_user.email.lower())
    )
    if not is_owner and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to cancel this order.")

    if (order.status or "").lower() != "pending":
        raise HTTPException(status_code=400, detail="Only pending orders can be cancelled.")

    items_to_restore = [(item.product_id, int(item.quantity)) for item in (order.items or [])]
    order.status = "Cancelled"
    db.commit()
    db.refresh(order)

    _rollback_stock(items_to_restore)
    return order


@router.get("/{order_id}/invoice")
def download_order_invoice(
    order_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate and stream a PDF invoice for a specific customer order."""
    order = db.query(models.OrderModel).filter(models.OrderModel.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

    is_owner = (
        (current_user.id is not None and order.user_id == current_user.id)
        or (order.customer_email and order.customer_email.lower() == current_user.email.lower())
    )
    if not is_owner and current_user.role not in {"admin", "order_manager", "manager"}:
        raise HTTPException(status_code=403, detail="Not authorized to view this invoice.")

    try:
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)
        _, height = letter

        p.setFont("Helvetica-Bold", 20)
        p.drawString(50, height - 50, "MidOnline Invoice")

        p.setFont("Helvetica", 10)
        p.drawString(50, height - 70, f"Order ID: #{order.id}")

        created_at = getattr(order, "created_at", None)
        date_str = created_at.strftime("%Y-%m-%d %H:%M") if isinstance(created_at, datetime.datetime) else "N/A"
        p.drawString(50, height - 85, f"Date: {date_str}")
        p.drawString(50, height - 100, f"Status: {order.status or 'Pending'}")

        p.setFont("Helvetica-Bold", 12)
        p.drawString(50, height - 135, "Shipping Address:")
        p.setFont("Helvetica", 10)
        p.drawString(50, height - 150, f"{order.first_name or ''} {order.last_name or ''}".strip() or "N/A")
        p.drawString(50, height - 165, f"{order.address or 'N/A'}, {order.city or ''}")
        p.drawString(50, height - 180, f"{order.country or ''} (Phone: {order.phone or 'N/A'})")

        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, height - 220, "Product")
        p.drawString(300, height - 220, "Qty")
        p.drawString(380, height - 220, "Price")
        p.drawString(460, height - 220, "Total")
        p.line(50, height - 225, 550, height - 225)

        y = height - 245
        p.setFont("Helvetica", 10)
        for item in (order.items or []):
            title = getattr(item, "product_title", None) or getattr(item, "title", None) or f"Product #{item.product_id}"
            price = float(getattr(item, "price", 0.0) or 0.0)
            qty = int(getattr(item, "quantity", 1) or 1)

            p.drawString(50, y, str(title)[:40])
            p.drawString(300, y, str(qty))
            p.drawString(380, y, f"${price:.2f}")
            p.drawString(460, y, f"${(price * qty):.2f}")
            y -= 20

            if y < 50:
                p.showPage()
                y = height - 50

        p.line(50, y - 5, 550, y - 5)
        p.setFont("Helvetica-Bold", 12)
        total_amt = float(getattr(order, "total_amount", 0.0) or 0.0)
        p.drawString(380, y - 25, "Total Amount:")
        p.drawString(460, y - 25, f"${total_amt:.2f}")

        p.showPage()
        p.save()
        buffer.seek(0)

        return Response(
            buffer.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=invoice_order_{order.id}.pdf"},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF Generation Error: {str(exc)}")