from fastapi import APIRouter, Depends

from app import db
from app.auth import get_current_user_id
from app.models.schemas import StatsOut

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
def get_stats(user_id: str = Depends(get_current_user_id)):
    return StatsOut(**db.get_stats(user_id))
