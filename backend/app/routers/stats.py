from fastapi import APIRouter

from app import db
from app.models.schemas import StatsOut

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
def get_stats():
    return StatsOut(**db.get_stats())
