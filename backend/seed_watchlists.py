"""Seed watchlists with categorized assets via the API."""
import httpx
import asyncio

BASE = "http://localhost:7000/api"

WATCHLISTS = {
    "Energy": [
        ("XOM", "Exxon Mobil", "stock"),
        ("CVX", "Chevron", "stock"),
        ("NMDP", "NewMed Energy", "stock"),
        ("DGRP", "Delek Group", "stock"),
        ("LNG", "Cheniere Energy", "stock"),
        ("EQT", "EQT Corporation", "stock"),
        ("NEXT", "NextDecade", "stock"),
        ("CCJ", "Cameco", "stock"),
        ("U-UN.TO", "Sprott Physical Uranium Trust", "etf"),
        ("UEC", "Uranium Energy", "stock"),
    ],
    "Metals": [
        ("GLD", "SPDR Gold Shares", "etf"),
        ("NEM", "Newmont", "stock"),
        ("B", "Barrick Mining", "stock"),
        ("FCX", "Freeport-McMoRan", "stock"),
        ("SCCO", "Southern Copper", "stock"),
        ("SLV", "iShares Silver Trust", "etf"),
        ("PAAS", "Pan American Silver", "stock"),
        ("MP", "MP Materials", "stock"),
        ("LYC.AX", "Lynas Rare Earths", "stock"),
    ],
    "Defense": [
        ("LMT", "Lockheed Martin", "stock"),
        ("NOC", "Northrop Grumman", "stock"),
        ("RTX", "RTX Corporation", "stock"),
        ("GD", "General Dynamics", "stock"),
        ("PLTR", "Palantir", "stock"),
        ("AVAV", "AeroVironment", "stock"),
        ("KTOS", "Kratos Defense", "stock"),
        ("ESLT", "Elbit Systems", "stock"),
    ],
    "Health": [
        ("UNH", "UnitedHealth", "stock"),
        ("JNJ", "Johnson & Johnson", "stock"),
        ("ABBV", "AbbVie", "stock"),
        ("MRK", "Merck", "stock"),
        ("MDT", "Medtronic", "stock"),
        ("ISRG", "Intuitive Surgical", "stock"),
        ("BSX", "Boston Scientific", "stock"),
        ("IBB", "iShares Biotechnology ETF", "etf"),
    ],
    "Tech": [
        ("NVDA", "Nvidia", "stock"),
        ("AMD", "AMD", "stock"),
        ("AVGO", "Broadcom", "stock"),
        ("ANET", "Arista Networks", "stock"),
        ("SMCI", "Super Micro Computer", "stock"),
        ("MSFT", "Microsoft", "stock"),
        ("GOOGL", "Alphabet", "stock"),
        ("AMZN", "Amazon", "stock"),
        ("META", "Meta Platforms", "stock"),
        ("ASML", "ASML", "stock"),
        ("AMAT", "Applied Materials", "stock"),
        ("LRCX", "Lam Research", "stock"),
        ("CRWD", "CrowdStrike", "stock"),
        ("PANW", "Palo Alto Networks", "stock"),
        ("ZS", "Zscaler", "stock"),
    ],
    "Infrastructure": [
        ("ETN", "Eaton", "stock"),
        ("GEV", "GE Vernova", "stock"),
        ("VRT", "Vertiv", "stock"),
        ("PWR", "Quanta Services", "stock"),
        ("CAT", "Caterpillar", "stock"),
        ("VMC", "Vulcan Materials", "stock"),
        ("MLM", "Martin Marietta", "stock"),
        ("NSC", "Norfolk Southern", "stock"),
        ("UNP", "Union Pacific", "stock"),
        ("BIP", "Brookfield Infrastructure", "stock"),
    ],
    "Israel Focus": [
        ("ESLT", "Elbit Systems", "stock"),
        ("NMDP", "NewMed Energy", "stock"),
    ],
}


async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        for wl_name, assets in WATCHLISTS.items():
            print(f"\n--- Creating watchlist: {wl_name} ---")

            # Create watchlist
            resp = await client.post(f"{BASE}/watchlist/", json={"name": wl_name})
            if resp.status_code == 201:
                wl = resp.json()
                wl_id = wl["id"]
                print(f"  Created: {wl_name} ({wl_id})")
            else:
                print(f"  Failed to create watchlist: {resp.status_code} {resp.text}")
                continue

            for symbol, name, asset_type in assets:
                # Find or create asset
                asset_resp = await client.get(f"{BASE}/assets/symbol/{symbol}")
                if asset_resp.status_code == 200:
                    asset_id = asset_resp.json()["id"]
                else:
                    # Create asset
                    create_resp = await client.post(f"{BASE}/assets/", json={
                        "symbol": symbol,
                        "name": name,
                        "exchange": "",
                        "currency": "USD",
                        "asset_type": asset_type,
                        "market_region": "US",
                        "provider_ids": {},
                    })
                    if create_resp.status_code == 201:
                        asset_id = create_resp.json()["id"]
                    else:
                        print(f"  Skip {symbol}: {create_resp.status_code} {create_resp.text}")
                        continue

                # Add to watchlist
                add_resp = await client.post(f"{BASE}/watchlist/{wl_id}/items", json={
                    "assetId": asset_id,
                })
                if add_resp.status_code == 201:
                    print(f"  + {symbol}")
                else:
                    print(f"  Skip {symbol}: {add_resp.status_code}")

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
