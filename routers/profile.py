from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies.auth import require_api_key
from models.database.profile import UserProfile
from models.profile import ProfileIn, ProfileOut, ProfilePatch
from services.profile_service import get_profile, patch_profile, upsert_profile

router = APIRouter(prefix="/api/v1", tags=["profile"])


def _profile_out(profile: UserProfile) -> ProfileOut:
    return ProfileOut(
        id=str(profile.id),
        embedding_hash=profile.embedding_hash,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        **profile.payload,
    )


@router.get("/profile", response_model=ProfileOut, status_code=status.HTTP_200_OK)
async def get_user_profile(
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> ProfileOut:
    profile = await get_profile(db)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="profile not found",
        )
    return _profile_out(profile)


@router.patch("/profile", response_model=ProfileOut, status_code=status.HTTP_200_OK)
async def patch_user_profile(
    patch: ProfilePatch,
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> ProfileOut:
    profile = await patch_profile(db, patch)
    return _profile_out(profile)


@router.put("/profile", response_model=ProfileOut)
async def put_user_profile(
    profile_in: ProfileIn,
    response: Response,
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> ProfileOut:
    profile, created = await upsert_profile(db, profile_in)
    response.status_code = (
        status.HTTP_201_CREATED if created else status.HTTP_200_OK
    )
    return _profile_out(profile)
