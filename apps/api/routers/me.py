"""Профиль + креды KBTU (uninet)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from apps.api.auth import current_user
from packages.core import uninet
from packages.db import users
from packages.shared.crypto import encrypt_password

router = APIRouter()


@router.get("/me")
async def get_me(user=Depends(current_user)):
    return {"credentials_set": user["kbtu_email"] is not None, "email": user["kbtu_email"]}


@router.put("/me/credentials")
async def put_credentials(request: Request, user=Depends(current_user)):
    body = await request.json()
    email, password = body.get("email"), body.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or not email or not password:
        raise HTTPException(422, "body must be {email: str, password: str}")
    # Проверяем вход сразу: InvalidCredentials -> 409 bad_credentials (обработчик в main.py).
    await uninet.session(email, password).login()
    await users.set_credentials(request.app.state.pool, user["id"], email, encrypt_password(password))
    return {"credentials_set": True, "email": email}


@router.delete("/me/credentials")
async def delete_credentials(request: Request, user=Depends(current_user)):
    await users.set_credentials(request.app.state.pool, user["id"], None, None)
    return {"credentials_set": False, "email": None}
