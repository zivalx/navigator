import React, { createContext, useContext, useState, useCallback, ReactNode, useEffect } from 'react';
import { MarketCard, MarketCardData, DataSource, defaultMarketCards } from '@/lib/marketCardTypes';

interface MarketCardsContextType {
  cards: MarketCardData[];
  addCard: (symbol: string, name: string, dataSource?: DataSource, region?: 'US' | 'EU' | 'ASIA') => void;
  removeCard: (id: string) => void;
  updateCard: (id: string, updates: Partial<MarketCard>) => void;
  reorderCards: (fromIndex: number, toIndex: number) => void;
  moveCard: (id: string, direction: 'up' | 'down' | 'left' | 'right') => void;
  isEditMode: boolean;
  setEditMode: (mode: boolean) => void;
}

const MarketCardsContext = createContext<MarketCardsContextType | undefined>(undefined);

// Mock price data for demo mode
const generateMockPrice = (symbol: string) => {
  const basePrices: Record<string, number> = {
    SPY: 478.23, QQQ: 405.67, DIA: 378.45, AAPL: 189.45, MSFT: 378.91,
    GOOGL: 141.23, NVDA: 495.22, TSLA: 248.50, AMZN: 178.35, META: 356.78,
    BTC: 43567.89, ETH: 2345.67, VTI: 245.67, VOO: 438.90, IWM: 198.45,
  };
  const basePrice = basePrices[symbol] || 100 + Math.random() * 200;
  const change = (Math.random() - 0.5) * 10;
  const changePercent = (change / basePrice) * 100;
  
  return {
    price: basePrice + change,
    change,
    changePercent,
    currency: 'USD',
    lastUpdated: new Date(),
  };
};

export function MarketCardsProvider({ children }: { children: ReactNode }) {
  const [cards, setCards] = useState<MarketCard[]>(() => {
    const saved = localStorage.getItem('marketCards');
    return saved ? JSON.parse(saved) : defaultMarketCards;
  });
  const [isEditMode, setEditMode] = useState(false);

  // Save to localStorage
  useEffect(() => {
    localStorage.setItem('marketCards', JSON.stringify(cards));
  }, [cards]);

  // Cards with price data
  const cardsWithData: MarketCardData[] = cards
    .sort((a, b) => a.order - b.order)
    .map(card => ({
      ...card,
      ...generateMockPrice(card.symbol),
    }));

  const addCard = useCallback((symbol: string, name: string, dataSource: DataSource = 'demo', region?: 'US' | 'EU' | 'ASIA') => {
    const newCard: MarketCard = {
      id: `card_${Date.now()}`,
      symbol: symbol.toUpperCase(),
      name,
      dataSource,
      order: cards.length,
      region,
    };
    setCards(prev => [...prev, newCard]);
  }, [cards.length]);

  const removeCard = useCallback((id: string) => {
    setCards(prev => {
      const filtered = prev.filter(c => c.id !== id);
      return filtered.map((c, i) => ({ ...c, order: i }));
    });
  }, []);

  const updateCard = useCallback((id: string, updates: Partial<MarketCard>) => {
    setCards(prev => prev.map(c => c.id === id ? { ...c, ...updates } : c));
  }, []);

  const reorderCards = useCallback((fromIndex: number, toIndex: number) => {
    setCards(prev => {
      const sorted = [...prev].sort((a, b) => a.order - b.order);
      const [removed] = sorted.splice(fromIndex, 1);
      sorted.splice(toIndex, 0, removed);
      return sorted.map((c, i) => ({ ...c, order: i }));
    });
  }, []);

  const moveCard = useCallback((id: string, direction: 'up' | 'down' | 'left' | 'right') => {
    setCards(prev => {
      const sorted = [...prev].sort((a, b) => a.order - b.order);
      const currentIndex = sorted.findIndex(c => c.id === id);
      if (currentIndex === -1) return prev;

      let newIndex = currentIndex;
      if (direction === 'left' || direction === 'up') {
        newIndex = Math.max(0, currentIndex - 1);
      } else {
        newIndex = Math.min(sorted.length - 1, currentIndex + 1);
      }

      if (newIndex === currentIndex) return prev;

      const [removed] = sorted.splice(currentIndex, 1);
      sorted.splice(newIndex, 0, removed);
      return sorted.map((c, i) => ({ ...c, order: i }));
    });
  }, []);

  return (
    <MarketCardsContext.Provider value={{
      cards: cardsWithData,
      addCard,
      removeCard,
      updateCard,
      reorderCards,
      moveCard,
      isEditMode,
      setEditMode,
    }}>
      {children}
    </MarketCardsContext.Provider>
  );
}

export function useMarketCards() {
  const context = useContext(MarketCardsContext);
  if (context === undefined) {
    throw new Error('useMarketCards must be used within a MarketCardsProvider');
  }
  return context;
}
