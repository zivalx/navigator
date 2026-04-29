"""
Seed script to populate database with initial data.

Run with: python seed.py
"""
import asyncio
from datetime import datetime, timedelta
import uuid

from app.database import SessionLocal, init_db
from app.models.asset import Asset, AssetType, MarketRegion, Currency
from app.models.holding import HoldingLot
from app.models.watchlist import Watchlist, WatchlistItem


def seed_assets(db):
    """Seed initial assets."""
    print("🌱 Seeding assets...")

    assets = [
        # US Stocks
        Asset(id=str(uuid.uuid4()), symbol="AAPL", name="Apple Inc.", exchange="NASDAQ", currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.US, provider_ids={"polygon": "AAPL", "finnhub": "AAPL"}),
        Asset(id=str(uuid.uuid4()), symbol="MSFT", name="Microsoft Corporation", exchange="NASDAQ", currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.US, provider_ids={"polygon": "MSFT"}),
        Asset(id=str(uuid.uuid4()), symbol="GOOGL", name="Alphabet Inc.", exchange="NASDAQ", currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.US, provider_ids={"polygon": "GOOGL"}),
        Asset(id=str(uuid.uuid4()), symbol="NVDA", name="NVIDIA Corporation", exchange="NASDAQ", currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.US, provider_ids={"polygon": "NVDA"}),
        Asset(id=str(uuid.uuid4()), symbol="TSLA", name="Tesla, Inc.", exchange="NASDAQ", currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.US, provider_ids={"polygon": "TSLA"}),
        Asset(id=str(uuid.uuid4()), symbol="AMZN", name="Amazon.com, Inc.", exchange="NASDAQ", currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.US, provider_ids={"polygon": "AMZN"}),
        Asset(id=str(uuid.uuid4()), symbol="META", name="Meta Platforms, Inc.", exchange="NASDAQ", currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.US, provider_ids={"polygon": "META"}),

        # ETFs
        Asset(id=str(uuid.uuid4()), symbol="VOO", name="Vanguard S&P 500 ETF", exchange="NYSE", currency=Currency.USD, asset_type=AssetType.ETF, market_region=MarketRegion.US, provider_ids={"polygon": "VOO"}),
        Asset(id=str(uuid.uuid4()), symbol="SPY", name="S&P 500 ETF", exchange="NYSE", currency=Currency.USD, asset_type=AssetType.ETF, market_region=MarketRegion.US, provider_ids={"polygon": "SPY"}),
        Asset(id=str(uuid.uuid4()), symbol="QQQ", name="Invesco QQQ Trust", exchange="NASDAQ", currency=Currency.USD, asset_type=AssetType.ETF, market_region=MarketRegion.US, provider_ids={"polygon": "QQQ"}),

        # Crypto
        Asset(id=str(uuid.uuid4()), symbol="BTC", name="Bitcoin", currency=Currency.USD, asset_type=AssetType.CRYPTO, market_region=MarketRegion.US, provider_ids={"coingecko": "bitcoin"}),
        Asset(id=str(uuid.uuid4()), symbol="ETH", name="Ethereum", currency=Currency.USD, asset_type=AssetType.CRYPTO, market_region=MarketRegion.US, provider_ids={"coingecko": "ethereum"}),

        # EU Stocks
        Asset(id=str(uuid.uuid4()), symbol="ASML", name="ASML Holding N.V.", exchange="NASDAQ", currency=Currency.EUR, asset_type=AssetType.STOCK, market_region=MarketRegion.EU, provider_ids={"polygon": "ASML"}),
        Asset(id=str(uuid.uuid4()), symbol="SAP", name="SAP SE", exchange="NYSE", currency=Currency.EUR, asset_type=AssetType.STOCK, market_region=MarketRegion.EU, provider_ids={"polygon": "SAP"}),

        # Asia Stocks
        Asset(id=str(uuid.uuid4()), symbol="TSM", name="Taiwan Semiconductor", exchange="NYSE", currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.ASIA, provider_ids={"polygon": "TSM"}),
        Asset(id=str(uuid.uuid4()), symbol="BABA", name="Alibaba Group", exchange="NYSE", currency=Currency.USD, asset_type=AssetType.STOCK, market_region=MarketRegion.ASIA, provider_ids={"polygon": "BABA"}),
    ]

    for asset in assets:
        # Check if already exists
        existing = db.query(Asset).filter(Asset.symbol == asset.symbol).first()
        if not existing:
            db.add(asset)

    db.commit()
    print(f"✅ Seeded {len(assets)} assets")
    return assets


def seed_holdings(db, assets):
    """Seed sample holdings."""
    print("🌱 Seeding holdings...")

    # Find assets by symbol
    aapl = next((a for a in assets if a.symbol == "AAPL"), None)
    msft = next((a for a in assets if a.symbol == "MSFT"), None)
    googl = next((a for a in assets if a.symbol == "GOOGL"), None)
    nvda = next((a for a in assets if a.symbol == "NVDA"), None)
    voo = next((a for a in assets if a.symbol == "VOO"), None)
    btc = next((a for a in assets if a.symbol == "BTC"), None)

    holdings = []

    if aapl:
        holdings.append(HoldingLot(
            id=str(uuid.uuid4()),
            asset_id=aapl.id,
            quantity=50,
            avg_cost=145.50,
            cost_currency=Currency.USD,
            account_name="Interactive Brokers",
            tags=["tech"],
            purchase_date=datetime.now() - timedelta(days=200),
        ))
        holdings.append(HoldingLot(
            id=str(uuid.uuid4()),
            asset_id=aapl.id,
            quantity=25,
            avg_cost=175.00,
            cost_currency=Currency.USD,
            account_name="Fidelity",
            tags=["tech"],
            purchase_date=datetime.now() - timedelta(days=50),
        ))

    if msft:
        holdings.append(HoldingLot(
            id=str(uuid.uuid4()),
            asset_id=msft.id,
            quantity=30,
            avg_cost=280.00,
            cost_currency=Currency.USD,
            account_name="Interactive Brokers",
            tags=["tech"],
            purchase_date=datetime.now() - timedelta(days=150),
        ))

    if googl:
        holdings.append(HoldingLot(
            id=str(uuid.uuid4()),
            asset_id=googl.id,
            quantity=15,
            avg_cost=125.00,
            cost_currency=Currency.USD,
            account_name="Schwab",
            tags=["tech"],
            purchase_date=datetime.now() - timedelta(days=120),
        ))

    if nvda:
        holdings.append(HoldingLot(
            id=str(uuid.uuid4()),
            asset_id=nvda.id,
            quantity=25,
            avg_cost=450.00,
            cost_currency=Currency.USD,
            account_name="IRA - Fidelity",
            tags=["tech", "ai"],
            purchase_date=datetime.now() - timedelta(days=300),
        ))

    if voo:
        holdings.append(HoldingLot(
            id=str(uuid.uuid4()),
            asset_id=voo.id,
            quantity=100,
            avg_cost=380.00,
            cost_currency=Currency.USD,
            account_name="IRA - Fidelity",
            tags=["index"],
            purchase_date=datetime.now() - timedelta(days=700),
        ))

    if btc:
        holdings.append(HoldingLot(
            id=str(uuid.uuid4()),
            asset_id=btc.id,
            quantity=0.5,
            avg_cost=42000.00,
            cost_currency=Currency.USD,
            account_name="Coinbase",
            tags=["crypto"],
            purchase_date=datetime.now() - timedelta(days=250),
        ))

    for holding in holdings:
        db.add(holding)

    db.commit()
    print(f"✅ Seeded {len(holdings)} holdings")


def seed_watchlist(db, assets):
    """Seed sample watchlist."""
    print("🌱 Seeding watchlist...")

    watchlist = Watchlist(
        id=str(uuid.uuid4()),
        name="Main Watchlist"
    )
    db.add(watchlist)
    db.commit()

    # Find some assets
    tsla = next((a for a in assets if a.symbol == "TSLA"), None)
    amzn = next((a for a in assets if a.symbol == "AMZN"), None)
    meta = next((a for a in assets if a.symbol == "META"), None)

    items = []

    if tsla:
        items.append(WatchlistItem(
            id=str(uuid.uuid4()),
            watchlist_id=watchlist.id,
            asset_id=tsla.id,
            notes="Wait for pullback",
            target_price=220.00,
        ))

    if amzn:
        items.append(WatchlistItem(
            id=str(uuid.uuid4()),
            watchlist_id=watchlist.id,
            asset_id=amzn.id,
            notes="Strong cloud growth",
        ))

    if meta:
        items.append(WatchlistItem(
            id=str(uuid.uuid4()),
            watchlist_id=watchlist.id,
            asset_id=meta.id,
            notes="AI investments paying off",
            target_price=550.00,
        ))

    for item in items:
        db.add(item)

    db.commit()
    print(f"✅ Seeded watchlist with {len(items)} items")


def main():
    """Main seed function."""
    print("🚀 Starting database seed...")

    # Initialize database
    init_db()

    # Create session
    db = SessionLocal()

    try:
        # Seed data
        assets = seed_assets(db)
        seed_holdings(db, assets)
        seed_watchlist(db, assets)

        print("\n🎉 Database seeded successfully!")
        print("\nYou can now:")
        print("  - Start the API: uvicorn app.main:app --reload")
        print("  - View docs: http://localhost:7000/docs")
        print("  - Get portfolio: http://localhost:7000/api/portfolio/summary")

    except Exception as e:
        print(f"\n❌ Error seeding database: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
