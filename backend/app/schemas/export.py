"""Response contract for GET /api/export/positions (snake_case, versioned)."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ExportedAsset(BaseModel):
    navigator_asset_id: str
    symbol: str
    name: str
    exchange: Optional[str] = None
    asset_type: str
    market_region: str
    currency: str
    held: bool
    total_quantity: Optional[float] = None
    avg_cost: Optional[float] = None
    avg_cost_currency: Optional[str] = None
    cost_basis_complete: bool
    cost_basis_issue: Optional[str] = None
    first_purchase_date: Optional[str] = None
    watchlists: List[str]


class PositionsExport(BaseModel):
    schema_version: int = 1
    generated_at: datetime
    asset_count: int
    assets: List[ExportedAsset]
