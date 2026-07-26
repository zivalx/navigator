export type AssetType = 'stock' | 'etf' | 'crypto' | 'fund' | 'other';
export type MarketRegion = 'US' | 'EU' | 'ASIA';
export type Currency = 'USD' | 'EUR' | 'GBP' | 'JPY' | 'CHF';

export interface Asset {
  id: string;
  symbol: string;
  name: string;
  exchange?: string;
  currency: Currency;
  assetType: AssetType;
  marketRegion: MarketRegion;
  providerIds?: Record<string, string>;
}

export interface HoldingLot {
  id: string;
  assetId: string;
  quantity: number;
  avgCost: number;
  costCurrency: Currency;
  accountName: string;
  tags?: string[];
  purchaseDate: Date;
  createdAt: Date;
}

export interface CashBalance {
  id: string;
  currency: Currency;
  amount: number;
  accountName: string;
}

export interface WatchlistItem {
  id: string;
  watchlistId: string;
  assetId: string;
  notes?: string;
  targetPrice?: number;
  createdAt: Date;
  currentPrice?: number;
  priceChange?: number;
  priceChangePercent?: number;
  asset?: Asset;
}

export interface Watchlist {
  id: string;
  name: string;
  createdAt: Date;
}

export interface PriceSnapshot {
  id: string;
  assetId: string;
  price: number;
  currency: Currency;
  timestamp: Date;
  source: string;
}

export interface NewsItem {
  id: string;
  assetId?: string;
  title: string;
  summary: string;
  url: string;
  publisher: string;
  publishedAt: Date;
  sentimentScore: number; // -1 to 1
  relevanceScore: number; // 0 to 1
}

export interface PortfolioSnapshot {
  timestamp: Date;
  navBaseCurrency: Currency;
  navValue: number;
  dailyPnL: number;
  dailyPnLPercent: number;
}

export interface FxRate {
  id: string;
  baseCurrency: Currency;
  quoteCurrency: Currency;
  rate: number;
  timestamp: Date;
}

// Computed types for UI
export interface HoldingWithAsset extends HoldingLot {
  asset: Asset;
  currentPrice?: number;
  priceChange?: number;
  priceChangePercent?: number;
  marketValue?: number;
  unrealizedPnL?: number;
  unrealizedPnLPercent?: number;
  weight?: number;
}

// Grouped holdings by asset for display
export interface GroupedHolding {
  assetId: string;
  asset: Asset;
  lots: HoldingWithAsset[];
  totalQuantity: number;
  avgCost: number;
  currentPrice?: number;
  priceChange?: number;
  priceChangePercent?: number;
  marketValue?: number;
  unrealizedPnL?: number;
  unrealizedPnLPercent?: number;
  weight?: number;
}

export interface WatchlistItemWithAsset extends WatchlistItem {
  asset: Asset;
  currentPrice?: number;
  priceChange?: number;
  priceChangePercent?: number;
  change1d?: number;
  change1m?: number;
  change6m?: number;
}

export interface PortfolioSummary {
  totalNav: number;
  baseCurrency: Currency;
  dailyPnL: number;
  dailyPnLPercent: number;
  weeklyPnLPercent?: number;
  monthlyPnLPercent?: number;
  totalCost: number;
  totalUnrealizedPnL: number;
  totalUnrealizedPnLPercent: number;
  lastUpdated: Date;
}

export interface MarketMover {
  asset: Asset;
  price: number;
  change: number;
  changePercent: number;
}

export interface Alert {
  id: string;
  type: 'missing_price' | 'unmapped_symbol' | 'fx_missing' | 'api_error';
  message: string;
  assetId?: string;
  timestamp: Date;
}

// Market indicators (Fear & Greed, VIX, indices, rates, fx, commodities, crypto, breadth)
export type IndicatorCategory =
  | 'sentiment'
  | 'volatility'
  | 'breadth'
  | 'index'
  | 'rates'
  | 'fx'
  | 'commodities'
  | 'crypto';

export type IndicatorRating = 'extreme_fear' | 'fear' | 'neutral' | 'greed' | 'extreme_greed';

export interface MarketIndicator {
  key: string;
  label: string;
  category: IndicatorCategory;
  value: number | null;
  unit: string;
  rating: IndicatorRating | null;
  change: number | null;
  change_pct: number | null;
  source: string;
  error: string | null;
}

export interface IndicatorsResponse {
  as_of: string;
  indicators: MarketIndicator[];
}

// Price alerts / stop-loss (spec: docs/superpowers/specs/2026-07-23-price-alerts-design.md)
export type PriceAlertRule = 'price_below' | 'price_above';
export type PriceAlertStatus = 'active' | 'triggered' | 'unacknowledged';

export interface PriceAlert {
  id: string;
  assetId: string;
  symbol: string;
  name: string;
  rule: PriceAlertRule;
  threshold: number;
  note?: string;
  isActive: boolean;
  createdAt: Date;
  triggeredAt?: Date | null;
  triggeredPrice?: number | null;
  acknowledgedAt?: Date | null;
}

export interface CreatePriceAlertPayload {
  assetId?: string;
  symbol?: string;
  rule: PriceAlertRule;
  threshold: number;
  note?: string;
}

export interface UpdatePriceAlertPayload {
  rule?: PriceAlertRule;
  threshold?: number;
  note?: string;
  isActive?: boolean;
}

// Portfolio NAV history (dashboard performance chart)
export type PortfolioHistoryPeriod = '1w' | '1m' | '3m' | '6m' | '1y';

export interface PortfolioHistoryPoint {
  date: string;
  nav: number;
  pnl: number;
  pnl_pct: number;
}

export interface PortfolioHistoryResponse {
  base_currency: Currency;
  period: PortfolioHistoryPeriod;
  points: PortfolioHistoryPoint[];
}
