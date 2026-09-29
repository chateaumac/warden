from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("")
def list_profiles(request: Request) -> list[dict]:
    profiles = request.app.state.profiles
    return [p.dump() for p in sorted(profiles.values(), key=lambda p: p.name.lower())]


@router.get("/{profile_id}")
def get_profile(profile_id: str, request: Request) -> dict:
    profile = request.app.state.profiles.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"profile {profile_id!r} not found")
    return profile.dump()
