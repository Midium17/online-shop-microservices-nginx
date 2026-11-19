import os
from typing import Callable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.permissions import ROLE_PERMISSIONS, Permission

SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-ecommerce-key-change-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    id: Optional[int] = None
    email: str
    role: str = "customer"


def _decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub") or payload.get("email")
        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload: missing subject.",
            )
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[CurrentUser]:
    if credentials is None or not credentials.credentials:
        return None
    payload = _decode_token(credentials.credentials)
    raw_id = payload.get("user_id") or payload.get("id")
    user_id = int(raw_id) if raw_id is not None else None
    return CurrentUser(
        id=user_id,
        email=payload.get("sub") or payload.get("email"),
        role=payload.get("role", "customer"),
    )


def get_current_user(
    current_user: Optional[CurrentUser] = Depends(get_optional_current_user),
) -> CurrentUser:
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


def get_current_admin_user(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return current_user


def require_permission(permission: Permission | str) -> Callable:
    perm_value = permission.value if isinstance(permission, Permission) else str(permission)

    def permission_checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        role_perms = ROLE_PERMISSIONS.get(current_user.role, set())
        normalized_perms = {
            p.value if isinstance(p, Permission) else str(p) for p in role_perms
        }
        if perm_value not in normalized_perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {perm_value}",
            )
        return current_user

    return permission_checker