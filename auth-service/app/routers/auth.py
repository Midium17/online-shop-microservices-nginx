import os
from typing import List, Optional
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.dependencies import get_current_user
from app.permissions import ALL_PERMISSIONS, ROLE_PERMISSIONS
from app.security import create_access_token, get_password_hash, verify_password

ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://127.0.0.1:8003")

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


def _require_admin(current_user: models.UserModel) -> None:
    if current_user.role != models.UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


@router.post("/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(models.UserModel).filter(models.UserModel.email == user.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user_role = user.role or models.UserRole.CUSTOMER.value
    new_user = models.UserModel(
        email=user.email,
        hashed_password=get_password_hash(user.password),
        role=user_role,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=schemas.Token)
def login(credentials: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.UserModel).filter(models.UserModel.email == credentials.email).first()
    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.email, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=schemas.UserResponse)
def get_me(current_user: models.UserModel = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=schemas.UserResponse)
def update_my_profile(
    profile_update: schemas.UserProfileUpdate,
    current_user: models.UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    for field in ("first_name", "last_name", "phone", "address", "city", "postal_code", "country"):
        value = getattr(profile_update, field, None)
        if value is not None and hasattr(current_user, field):
            setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/admin/users", response_model=List[schemas.UserResponse])
def get_all_users(
    db: Session = Depends(get_db),
    current_user: models.UserModel = Depends(get_current_user),
):
    _require_admin(current_user)
    return db.query(models.UserModel).all()


@router.post("/admin/users", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def admin_create_user(
    user: schemas.UserCreate,
    db: Session = Depends(get_db),
    current_user: models.UserModel = Depends(get_current_user),
):
    _require_admin(current_user)
    if db.query(models.UserModel).filter(models.UserModel.email == user.email).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    new_user = models.UserModel(
        email=user.email,
        hashed_password=get_password_hash(user.password),
        role=user.role or models.UserRole.CUSTOMER.value,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.patch("/admin/users/{user_id}/role", response_model=schemas.UserResponse)
def update_user_role(
    user_id: int,
    role_update: schemas.UserRoleUpdate,
    db: Session = Depends(get_db),
    current_user: models.UserModel = Depends(get_current_user),
):
    _require_admin(current_user)
    target_user = db.query(models.UserModel).filter(models.UserModel.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target_user.role = role_update.role
    db.commit()
    db.refresh(target_user)
    return target_user


@router.delete("/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.UserModel = Depends(get_current_user),
):
    _require_admin(current_user)
    if current_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    target_user = db.query(models.UserModel).filter(models.UserModel.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    db.delete(target_user)
    db.commit()
    return None


@router.get("/admin/permissions")
def get_role_permissions(current_user: models.UserModel = Depends(get_current_user)):
    _require_admin(current_user)
    return {
        "permissions": list(ALL_PERMISSIONS),
        "role_permissions": {k: list(v) for k, v in ROLE_PERMISSIONS.items()},
    }


@router.put("/admin/permissions/{role}")
def update_role_permissions(
    role: str,
    permissions_payload: schemas.RolePermissionsUpdate,
    current_user: models.UserModel = Depends(get_current_user),
):
    _require_admin(current_user)
    if role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    ROLE_PERMISSIONS[role] = set(permissions_payload.permissions)
    return {
        "message": f"Permissions updated for role: {role}",
        "permissions": list(ROLE_PERMISSIONS[role]),
    }


def _fetch_all_orders(authorization: Optional[str]) -> list[dict]:
    headers = {"Authorization": authorization} if authorization else {}
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{ORDER_SERVICE_URL}/orders/", headers=headers)
            if resp.status_code == 200 and isinstance(resp.json(), list):
                return resp.json()
    except httpx.RequestError:
        pass
    return []


@router.get("/admin/customers")
def get_all_customers(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
    current_user: models.UserModel = Depends(get_current_user),
):
    allowed_roles = {models.UserRole.ADMIN.value, "order_manager", "inventory_manager", "manager"}
    if current_user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized access to customer directory",
        )

    users = (
        db.query(models.UserModel)
        .filter((models.UserModel.role == "customer") | (models.UserModel.role.is_(None)))
        .all()
    )

    all_orders = _fetch_all_orders(authorization)
    orders_by_email: dict[str, list[dict]] = {}
    for order in all_orders:
        email = (order.get("customer_email") or "").lower()
        if email:
            orders_by_email.setdefault(email, []).append(order)

    customer_analytics = []
    for user in users:
        user_orders = orders_by_email.get((user.email or "").lower(), [])
        total_orders = len(user_orders)
        total_spent = sum(float(o.get("total_amount") or o.get("total") or 0.0) for o in user_orders)
        latest_order = user_orders[-1] if user_orders else {}

        formatted_orders = []
        for o in user_orders:
            order_items = [
                {
                    "title": item.get("product_title") or item.get("title") or "Product",
                    "quantity": int(item.get("quantity") or 1),
                    "price": float(item.get("price") or 0.0),
                    "image_url": item.get("image_url"),
                }
                for item in (o.get("items") or [])
            ]
            formatted_orders.append(
                {
                    "id": o.get("id"),
                    "status": o.get("status") or "Completed",
                    "total_amount": float(o.get("total_amount") or o.get("total") or 0.0),
                    "items": order_items,
                }
            )

        customer_analytics.append(
            {
                "id": user.id,
                "email": user.email,
                "role": user.role or "customer",
                "first_name": getattr(user, "first_name", None) or latest_order.get("first_name") or "N/A",
                "last_name": getattr(user, "last_name", None) or latest_order.get("last_name") or "N/A",
                "phone": getattr(user, "phone", None) or latest_order.get("phone") or "No phone provided",
                "address": getattr(user, "address", None) or latest_order.get("address") or "N/A",
                "city": getattr(user, "city", None) or latest_order.get("city") or "N/A",
                "country": getattr(user, "country", None) or latest_order.get("country") or "N/A",
                "total_orders": total_orders,
                "total_spent": total_spent,
                "orders": formatted_orders,
            }
        )

    return customer_analytics


@router.put("/admin/customers/{customer_id}", response_model=schemas.UserResponse)
def update_customer(
    customer_id: int,
    user_update: dict,
    db: Session = Depends(get_db),
    current_user: models.UserModel = Depends(get_current_user),
):
    if current_user.role not in {models.UserRole.ADMIN.value, "order_manager"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized")

    customer = (
        db.query(models.UserModel)
        .filter(models.UserModel.id == customer_id, models.UserModel.role == "customer")
        .first()
    )
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    protected_fields = {"id", "hashed_password", "password", "role"}
    for key, value in user_update.items():
        if key not in protected_fields and hasattr(customer, key) and value is not None:
            setattr(customer, key, value)

    db.commit()
    db.refresh(customer)
    return customer


@router.delete("/admin/customers/{customer_id}")
def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: models.UserModel = Depends(get_current_user),
):
    _require_admin(current_user)
    customer = (
        db.query(models.UserModel)
        .filter(models.UserModel.id == customer_id, models.UserModel.role == "customer")
        .first()
    )
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")

    db.delete(customer)
    db.commit()
    return {"message": f"Customer #{customer_id} deleted successfully"}