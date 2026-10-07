"""Auth-related endpoints: profile retrieval and upsert.

Actual signup/login/logout is handled by Supabase Auth on the frontend.
These endpoints manage the application profile linked to auth.users.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from auth.supabase import get_current_user, get_user_id
from db import prisma_client as db

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Return current user profile, creating one if it doesn't exist."""
    user_id = user["sub"]
    email = user.get("email", "")

    profile = await db.get_profile(user_id)
    if not profile:
        profile = await db.upsert_profile(user_id, full_name=email.split("@")[0] if email else None)

    return {
        "user_id": user_id,
        "email": email,
        "full_name": profile.get("full_name"),
        "avatar_url": profile.get("avatar_url"),
        "created_at": str(profile.get("created_at", "")),
    }
