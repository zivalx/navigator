import type { MarketRegion } from '@/lib/types';
export interface MarketCard {
  id: string;
  symbol: string;
  name: string;
  order: number;
  region?: MarketRegion;
  /** 'index' cards show points rather than a currency amount. */
  assetType?: 'index';
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
  { id: '7', symbol: 'TA35.TA', name: 'TA-35', order: 6, region: 'IL', assetType: 'index' },
  { id: '8', symbol: '^TA125.TA', name: 'TA-125', order: 7, region: 'IL', assetType: 'index' },
  { id: '9', symbol: 'TEVA.TA', name: 'Teva (Tel Aviv)', order: 8, region: 'IL' },
  { id: '10', symbol: 'LUMI.TA', name: 'Bank Leumi', order: 9, region: 'IL' },
];

/** Tel Aviv index cards added once to layouts saved before the Israel tab had any. */
export const ilIndexCards: MarketCard[] = defaultMarketCards.filter(
  (c) => c.region === 'IL' && c.assetType === 'index',
);

/** localStorage flag: the Israel index cards were offered to a saved layout. */
export const IL_CARDS_SEEDED_KEY = 'marketCardsIlSeeded';

/**
 * Append the Tel Aviv index cards to a layout saved before they existed. Runs
 * once per browser (the caller sets IL_CARDS_SEEDED_KEY), so removing them sticks.
 */
export function seedIsraelCards(saved: MarketCard[], alreadySeeded: boolean): MarketCard[] {
  if (alreadySeeded || saved.some((c) => c.region === 'IL')) return saved;
  const symbols = new Set(saved.map((c) => c.symbol));
  const nextOrder = saved.reduce((max, c) => Math.max(max, c.order), -1) + 1;
  const added = ilIndexCards
    .filter((c) => !symbols.has(c.symbol))
    .map((c, i) => ({ ...c, id: `il_${c.id}`, order: nextOrder + i }));
  return [...saved, ...added];
}
