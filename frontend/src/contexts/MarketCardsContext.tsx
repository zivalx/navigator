import React, { createContext, useContext, useState, useCallback, ReactNode, useEffect, useRef } from 'react';
import { MarketCard, MarketCardData, defaultMarketCards } from '@/lib/marketCardTypes';
import { api } from '@/lib/api';
import { useAppSettings } from '@/contexts/AppSettingsContext';

interface MarketCardsContextType {
  cards: MarketCardData[];
  addCard: (symbol: string, name: string, region?: 'US' | 'EU' | 'ASIA') => void;
  removeCard: (id: string) => void;
  updateCard: (id: string, updates: Partial<MarketCard>) => void;
  reorderCards: (fromIndex: number, toIndex: number) => void;
  moveCard: (id: string, direction: 'up' | 'down' | 'left' | 'right') => void;
  isEditMode: boolean;
  setEditMode: (mode: boolean) => void;
}

const MarketCardsContext = createContext<MarketCardsContextType | undefined>(undefined);

export function MarketCardsProvider({ children }: { children: ReactNode }) {
  const [cards, setCards] = useState<MarketCard[]>(() => {
    const saved = localStorage.getItem('marketCards');
    return saved ? JSON.parse(saved) : defaultMarketCards;
  });
  const [prices, setPrices] = useState<Record<string, { price: number; change: number; changePercent: number; currency: string }>>({});
  const [isEditMode, setEditMode] = useState(false);
  const fetchingRef = useRef(false);
  const { refreshIntervalMs } = useAppSettings();

  // Save to localStorage
  useEffect(() => {
    localStorage.setItem('marketCards', JSON.stringify(cards));
  }, [cards]);

  // Fetch real prices for all card symbols
  const fetchPrices = useCallback(async () => {
    if (fetchingRef.current || cards.length === 0) return;
    fetchingRef.current = true;
    try {
      const symbols = [...new Set(cards.map(c => c.symbol))];
      const quotes = await api.getQuotes(symbols);
      const newPrices: typeof prices = {};
      for (const [symbol, data] of Object.entries(quotes)) {
        if (data && typeof data === 'object' && 'price' in data) {
          newPrices[symbol] = {
            price: data.price,
            change: data.change ?? 0,
            changePercent: data.changePercent ?? 0,
            currency: data.currency ?? 'USD',
          };
        }
      }
      setPrices(newPrices);
    } catch (err) {
      // Silently fail — cards will show without prices
    } finally {
      fetchingRef.current = false;
    }
  }, [cards]);

  // Fetch on mount, then at the user-configured interval (disabled when "Manual only" is selected)
  useEffect(() => {
    fetchPrices();
    if (refreshIntervalMs === false) return;
    const interval = setInterval(fetchPrices, refreshIntervalMs);
    return () => clearInterval(interval);
  }, [fetchPrices, refreshIntervalMs]);

  // Cards with real price data
  const cardsWithData: MarketCardData[] = cards
    .sort((a, b) => a.order - b.order)
    .map(card => ({
      ...card,
      ...(prices[card.symbol] ?? {}),
      lastUpdated: new Date(),
    }));

  const addCard = useCallback((symbol: string, name: string, region?: 'US' | 'EU' | 'ASIA') => {
    const newCard: MarketCard = {
      id: `card_${Date.now()}`,
      symbol: symbol.toUpperCase(),
      name,
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
