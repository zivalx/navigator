from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class NewsItem(BaseModel):
    id: str
    asset_id: Optional[str] = Field(default=None, alias="assetId")
    title: str
    summary: str
    url: str
    publisher: str
    published_at: datetime = Field(alias="publishedAt")
    sentiment_score: float = Field(alias="sentimentScore")  # -1 to 1
    relevance_score: float = Field(alias="relevanceScore")  # 0 to 1

    class Config:
        from_attributes = True
        populate_by_name = True
