export interface MarketCard {
  id: string;
  symbol: string;
  name: string;
  order: number;
  region?: 'US' | 'EU' | 'ASIA';
}

export interface MarketCardData extends MarketCard {
  price?: number;
  change?: number;
  changePercent?: number;
  currency?: string;
  lastUpdated?: Date;
  error?: string;
}

export const defaultMarketCards: MarketCard[] = [
  { id: '1', symbol: 'SPY', name: 'S&P 500 ETF', order: 0, region: 'US' },
  { id: '2', symbol: 'QQQ', name: 'Nasdaq 100 ETF', order: 1, region: 'US' },
  { id: '3', symbol: 'DIA', name: 'Dow Jones ETF', order: 2, region: 'US' },
  { id: '4', symbol: 'AAPL', name: 'Apple Inc.', order: 3, region: 'US' },
  { id: '5', symbol: 'MSFT', name: 'Microsoft', order: 4, region: 'US' },
  { id: '6', symbol: 'GOOGL', name: 'Alphabet', order: 5, region: 'US' },
];
