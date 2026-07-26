import { IndicatorCategory } from './types';

export interface IndicatorMeta {
  key: string;
  label: string;
  category: IndicatorCategory;
}

// Mirrors the backend indicator registry (docs/superpowers/specs/2026-07-23-market-indicators-design.md)
export const indicatorRegistry: IndicatorMeta[] = [
  { key: 'fear_greed_stocks', label: 'Fear & Greed', category: 'sentiment' },
  { key: 'fear_greed_crypto', label: 'Crypto Fear & Greed', category: 'sentiment' },
  { key: 'vix', label: 'VIX', category: 'volatility' },
  { key: 's5fi', label: 'S&P 500 % Above 50-Day MA', category: 'breadth' },
  { key: 's5th', label: 'S&P 500 % Above 200-Day MA', category: 'breadth' },
  { key: 'sp500', label: 'S&P 500', category: 'index' },
  { key: 'nasdaq', label: 'Nasdaq', category: 'index' },
  { key: 'dow', label: 'Dow Jones', category: 'index' },
  { key: 'russell2000', label: 'Russell 2000', category: 'index' },
  { key: 'stoxx50', label: 'Euro Stoxx 50', category: 'index' },
  { key: 'dax', label: 'DAX', category: 'index' },
  { key: 'smi', label: 'SMI (Swiss)', category: 'index' },
  { key: 'nikkei', label: 'Nikkei 225', category: 'index' },
  { key: 'us10y', label: 'US 10Y Yield', category: 'rates' },
  { key: 'us30y', label: 'US 30Y Yield', category: 'rates' },
  { key: 'dxy', label: 'Dollar Index', category: 'fx' },
  { key: 'gold', label: 'Gold', category: 'commodities' },
  { key: 'oil_wti', label: 'Crude Oil (WTI)', category: 'commodities' },
  { key: 'btc', label: 'Bitcoin', category: 'crypto' },
];

export const indicatorMetaByKey: Record<string, IndicatorMeta> = indicatorRegistry.reduce(
  (acc, meta) => {
    acc[meta.key] = meta;
    return acc;
  },
  {} as Record<string, IndicatorMeta>
);

export const categoryOrder: IndicatorCategory[] = [
  'sentiment',
  'volatility',
  'breadth',
  'index',
  'rates',
  'fx',
  'commodities',
  'crypto',
];

export const categoryLabels: Record<IndicatorCategory, string> = {
  sentiment: 'Sentiment',
  volatility: 'Volatility',
  breadth: 'Breadth',
  index: 'Indices',
  rates: 'Rates',
  fx: 'FX',
  commodities: 'Commodities',
  crypto: 'Crypto',
};

export const sentimentKeys = ['fear_greed_stocks', 'fear_greed_crypto'];

export const defaultIndicatorKeys: string[] = [
  'fear_greed_stocks',
  'fear_greed_crypto',
  'vix',
  's5fi',
  'sp500',
  'nasdaq',
  'us10y',
  'dxy',
  'gold',
  'btc',
];
