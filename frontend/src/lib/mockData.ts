import { Asset, HoldingLot, WatchlistItem, Watchlist, NewsItem, PriceSnapshot, FxRate, Currency } from './types';

export const mockAssets: Asset[] = [
  { id: '1', symbol: 'AAPL', name: 'Apple Inc.', exchange: 'NASDAQ', currency: 'USD', assetType: 'stock', marketRegion: 'US' },
  { id: '2', symbol: 'MSFT', name: 'Microsoft Corporation', exchange: 'NASDAQ', currency: 'USD', assetType: 'stock', marketRegion: 'US' },
  { id: '3', symbol: 'GOOGL', name: 'Alphabet Inc.', exchange: 'NASDAQ', currency: 'USD', assetType: 'stock', marketRegion: 'US' },
  { id: '4', symbol: 'NVDA', name: 'NVIDIA Corporation', exchange: 'NASDAQ', currency: 'USD', assetType: 'stock', marketRegion: 'US' },
  { id: '5', symbol: 'TSLA', name: 'Tesla, Inc.', exchange: 'NASDAQ', currency: 'USD', assetType: 'stock', marketRegion: 'US' },
  { id: '6', symbol: 'AMZN', name: 'Amazon.com, Inc.', exchange: 'NASDAQ', currency: 'USD', assetType: 'stock', marketRegion: 'US' },
  { id: '7', symbol: 'META', name: 'Meta Platforms, Inc.', exchange: 'NASDAQ', currency: 'USD', assetType: 'stock', marketRegion: 'US' },
  { id: '8', symbol: 'VOO', name: 'Vanguard S&P 500 ETF', exchange: 'NYSE', currency: 'USD', assetType: 'etf', marketRegion: 'US' },
  { id: '9', symbol: 'BTC', name: 'Bitcoin', currency: 'USD', assetType: 'crypto', marketRegion: 'US' },
  { id: '10', symbol: 'ETH', name: 'Ethereum', currency: 'USD', assetType: 'crypto', marketRegion: 'US' },
  { id: '11', symbol: 'ASML', name: 'ASML Holding N.V.', exchange: 'NASDAQ', currency: 'EUR', assetType: 'stock', marketRegion: 'EU' },
  { id: '12', symbol: 'SAP', name: 'SAP SE', exchange: 'NYSE', currency: 'EUR', assetType: 'stock', marketRegion: 'EU' },
  { id: '13', symbol: 'TSM', name: 'Taiwan Semiconductor', exchange: 'NYSE', currency: 'USD', assetType: 'stock', marketRegion: 'ASIA' },
  { id: '14', symbol: 'BABA', name: 'Alibaba Group', exchange: 'NYSE', currency: 'USD', assetType: 'stock', marketRegion: 'ASIA' },
];

export const mockHoldings: HoldingLot[] = [
  { id: 'h1', assetId: '1', quantity: 50, avgCost: 145.50, costCurrency: 'USD', accountName: 'Interactive Brokers', tags: ['tech'], purchaseDate: new Date('2023-06-15'), createdAt: new Date('2023-06-15') },
  { id: 'h1b', assetId: '1', quantity: 25, avgCost: 175.00, costCurrency: 'USD', accountName: 'Fidelity', tags: ['tech'], purchaseDate: new Date('2024-02-10'), createdAt: new Date('2024-02-10') },
  { id: 'h2', assetId: '2', quantity: 30, avgCost: 280.00, costCurrency: 'USD', accountName: 'Interactive Brokers', tags: ['tech'], purchaseDate: new Date('2023-08-20'), createdAt: new Date('2023-08-20') },
  { id: 'h3', assetId: '3', quantity: 15, avgCost: 125.00, costCurrency: 'USD', accountName: 'Schwab', tags: ['tech'], purchaseDate: new Date('2023-09-10'), createdAt: new Date('2023-09-10') },
  { id: 'h4', assetId: '4', quantity: 25, avgCost: 450.00, costCurrency: 'USD', accountName: 'IRA - Fidelity', tags: ['tech', 'ai'], purchaseDate: new Date('2024-01-15'), createdAt: new Date('2024-01-15') },
  { id: 'h4b', assetId: '4', quantity: 10, avgCost: 120.00, costCurrency: 'USD', accountName: 'Interactive Brokers', tags: ['tech', 'ai'], purchaseDate: new Date('2024-06-20'), createdAt: new Date('2024-06-20') },
  { id: 'h5', assetId: '8', quantity: 100, avgCost: 380.00, costCurrency: 'USD', accountName: 'IRA - Fidelity', tags: ['index'], purchaseDate: new Date('2023-01-01'), createdAt: new Date('2023-01-01') },
  { id: 'h6', assetId: '9', quantity: 0.5, avgCost: 42000.00, costCurrency: 'USD', accountName: 'Coinbase', tags: ['crypto'], purchaseDate: new Date('2024-02-01'), createdAt: new Date('2024-02-01') },
  { id: 'h7', assetId: '11', quantity: 8, avgCost: 620.00, costCurrency: 'EUR', accountName: 'Degiro', tags: ['tech', 'eu'], purchaseDate: new Date('2024-03-01'), createdAt: new Date('2024-03-01') },
];

export const mockPrices: Record<string, { price: number; change: number; changePercent: number }> = {
  '1': { price: 196.42, change: 2.35, changePercent: 1.21 },
  '2': { price: 432.18, change: -3.82, changePercent: -0.88 },
  '3': { price: 178.56, change: 4.23, changePercent: 2.43 },
  '4': { price: 138.72, change: 5.67, changePercent: 4.26 },
  '5': { price: 248.93, change: -8.41, changePercent: -3.27 },
  '6': { price: 227.35, change: 1.89, changePercent: 0.84 },
  '7': { price: 612.45, change: 12.34, changePercent: 2.06 },
  '8': { price: 548.72, change: 2.15, changePercent: 0.39 },
  '9': { price: 94285.00, change: 1842.50, changePercent: 1.99 },
  '10': { price: 3412.80, change: -45.20, changePercent: -1.31 },
  '11': { price: 732.50, change: 8.90, changePercent: 1.23 },
  '12': { price: 245.80, change: -2.10, changePercent: -0.85 },
  '13': { price: 189.45, change: 3.22, changePercent: 1.73 },
  '14': { price: 85.32, change: -1.45, changePercent: -1.67 },
};

export const mockWatchlists: Watchlist[] = [
  { id: 'wl1', name: 'Main', createdAt: new Date('2024-01-01') },
  { id: 'wl2', name: 'Tech Picks', createdAt: new Date('2024-02-01') },
];

export const mockWatchlistItems: WatchlistItem[] = [
  { id: 'w1', watchlistId: 'wl1', assetId: '5', notes: 'Wait for pullback', targetPrice: 220.00, createdAt: new Date('2024-01-15') },
  { id: 'w2', watchlistId: 'wl1', assetId: '6', notes: 'Strong cloud growth', createdAt: new Date('2024-02-01') },
  { id: 'w3', watchlistId: 'wl1', assetId: '7', notes: 'Metaverse bet', targetPrice: 550.00, createdAt: new Date('2024-02-15') },
  { id: 'w4', watchlistId: 'wl2', assetId: '10', createdAt: new Date('2024-03-01') },
  { id: 'w5', watchlistId: 'wl2', assetId: '13', notes: 'AI chip demand', createdAt: new Date('2024-03-15') },
];

export const mockNews: NewsItem[] = [
  {
    id: 'n1',
    assetId: '4',
    title: 'NVIDIA Reports Record Q4 Revenue Driven by AI Demand',
    summary: 'NVIDIA Corporation announced record quarterly revenue of $22.1 billion, up 265% from a year ago, fueled by unprecedented demand for AI chips.',
    url: 'https://example.com/nvidia-q4',
    publisher: 'Reuters',
    publishedAt: new Date(Date.now() - 2 * 60 * 60 * 1000),
    sentimentScore: 0.85,
    relevanceScore: 0.95,
  },
  {
    id: 'n2',
    assetId: '1',
    title: 'Apple Vision Pro Sales Exceed Expectations',
    summary: 'Apple\'s new Vision Pro headset has sold over 200,000 units in its first week, beating analyst expectations.',
    url: 'https://example.com/apple-vision',
    publisher: 'Bloomberg',
    publishedAt: new Date(Date.now() - 5 * 60 * 60 * 1000),
    sentimentScore: 0.72,
    relevanceScore: 0.88,
  },
  {
    id: 'n3',
    assetId: '5',
    title: 'Tesla Faces Increased Competition in China',
    summary: 'Tesla\'s market share in China continues to decline as local EV makers BYD and NIO gain ground.',
    url: 'https://example.com/tesla-china',
    publisher: 'CNBC',
    publishedAt: new Date(Date.now() - 8 * 60 * 60 * 1000),
    sentimentScore: -0.45,
    relevanceScore: 0.82,
  },
  {
    id: 'n4',
    assetId: '9',
    title: 'Bitcoin ETF Sees Record Inflows',
    summary: 'Spot Bitcoin ETFs recorded $1.2 billion in net inflows yesterday, the highest single-day total since launch.',
    url: 'https://example.com/btc-etf',
    publisher: 'CoinDesk',
    publishedAt: new Date(Date.now() - 12 * 60 * 60 * 1000),
    sentimentScore: 0.78,
    relevanceScore: 0.91,
  },
  {
    id: 'n5',
    assetId: '2',
    title: 'Microsoft Azure Growth Slows Amid Cloud Competition',
    summary: 'Microsoft reported Azure revenue growth of 29%, slightly below analyst expectations of 31%.',
    url: 'https://example.com/msft-azure',
    publisher: 'WSJ',
    publishedAt: new Date(Date.now() - 24 * 60 * 60 * 1000),
    sentimentScore: -0.15,
    relevanceScore: 0.79,
  },
];

export const mockFxRates: FxRate[] = [
  { id: 'fx1', baseCurrency: 'USD', quoteCurrency: 'EUR', rate: 0.92, timestamp: new Date() },
  { id: 'fx2', baseCurrency: 'USD', quoteCurrency: 'GBP', rate: 0.79, timestamp: new Date() },
  { id: 'fx3', baseCurrency: 'USD', quoteCurrency: 'JPY', rate: 149.50, timestamp: new Date() },
  { id: 'fx4', baseCurrency: 'USD', quoteCurrency: 'CHF', rate: 0.88, timestamp: new Date() },
  { id: 'fx5', baseCurrency: 'EUR', quoteCurrency: 'USD', rate: 1.09, timestamp: new Date() },
];

export const marketIndices = {
  US: [
    { symbol: 'SPY', name: 'S&P 500 ETF', price: 594.82, change: 1.23 },
    { symbol: 'QQQ', name: 'Nasdaq 100 ETF', price: 518.45, change: 1.85 },
    { symbol: 'DIA', name: 'Dow Jones ETF', price: 428.12, change: 0.45 },
    { symbol: 'IWM', name: 'Russell 2000 ETF', price: 224.56, change: -0.32 },
  ],
  EU: [
    { symbol: 'EZU', name: 'Eurozone ETF', price: 48.92, change: 0.78 },
    { symbol: 'EWG', name: 'Germany ETF', price: 32.45, change: 0.92 },
    { symbol: 'EWU', name: 'UK ETF', price: 35.18, change: 0.15 },
  ],
  ASIA: [
    { symbol: 'EWJ', name: 'Japan ETF', price: 72.34, change: 1.12 },
    { symbol: 'FXI', name: 'China Large-Cap ETF', price: 28.67, change: -1.45 },
    { symbol: 'EWT', name: 'Taiwan ETF', price: 56.89, change: 2.34 },
  ],
};

export function getAssetById(id: string): Asset | undefined {
  return mockAssets.find(a => a.id === id);
}

export function getAssetBySymbol(symbol: string): Asset | undefined {
  return mockAssets.find(a => a.symbol.toLowerCase() === symbol.toLowerCase());
}

export function getPriceForAsset(assetId: string): { price: number; change: number; changePercent: number } | undefined {
  return mockPrices[assetId];
}

export function convertCurrency(amount: number, from: Currency, to: Currency): number {
  if (from === to) return amount;
  
  const rate = mockFxRates.find(
    r => (r.baseCurrency === from && r.quoteCurrency === to) ||
         (r.baseCurrency === to && r.quoteCurrency === from)
  );
  
  if (!rate) return amount;
  
  if (rate.baseCurrency === from) {
    return amount * rate.rate;
  } else {
    return amount / rate.rate;
  }
}
