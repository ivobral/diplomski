"""POST /api/evaluate — benchmark runner (Faza 4).

Stub. Stvarna implementacija pokreće BIRD-Mini benchmark s odabranim
eksperimentom (A/B/C/D) i vraća metrike (Exact Match, Execution Accuracy,
Latency, Security Rejection, …).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/evaluate", tags=["evaluation"])


@router.post("")
async def evaluate() -> dict:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Benchmark evaluacija dolazi u Fazi 4.",
    )
