import { usePrefetchOnHover } from "@/hooks/use-prefetch-on-hover";
import type { Coin } from "@/loadAllCoinsList";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

// ===== TYPES =====
type CoinSearchResultProps = {
  coin: Coin;
  onDropdownItemClick: (c: Coin) => void;
  prefetchFn?: (queryClient: QueryClient, coin: Coin) => any;
};

export default function CoinSearchResult({
  coin,
  onDropdownItemClick,
  prefetchFn = () => {},
}: CoinSearchResultProps) {
  // ===== REACT QUERY HOOKS =====
  const queryClient = useQueryClient();

  // ===== EVENT HANDLERS =====
  const { onMouseEnter, onMouseLeave } = usePrefetchOnHover(() =>
    prefetchFn(queryClient, coin),
  );

  return (
    <div
      className="flex justify-between gap-2 rounded-md border border-border bg-background px-2 py-0.5 cursor-pointer transition-colors hover:border-[#71717a] hover:bg-muted"
      onClick={() => onDropdownItemClick(coin)}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {/* ===== COIN INFO ===== */}
      <div className="flex gap-1.5 min-w-0">
        <img className="rounded-3xl size-7 shrink-0" src={coin.imgUrl} />
        <p className="truncate">{coin.name}</p>
      </div>

      {/* ===== TICKER ===== */}
      <p className="uppercase shrink-0">${coin.ticker}</p>
    </div>
  );
}
