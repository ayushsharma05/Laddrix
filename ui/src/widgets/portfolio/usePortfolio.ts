// One kind of portfolio figures (funds, holdings or trades) for the selected account, with a
// Refresh that asks the broker now. Pushes from the server keep them current otherwise.

import { useCallback, useState } from "react";
import type { PortfolioKind, PortfolioSnapshot } from "../../api/types";
import { useApp, useStore } from "../../app/context";

export function usePortfolio(kind: PortfolioKind): {
  accountId: string | null;
  snapshot: PortfolioSnapshot | undefined;
  missing: string | undefined;
  refreshing: boolean;
  refresh: () => void;
} {
  const { session } = useApp();
  const accountId = useStore((s) => s.accountId);
  const snapshot = useStore((s) => (s.accountId ? s.books[s.accountId]?.portfolio[kind] : undefined));
  const missing = useStore((s) => (s.accountId ? s.books[s.accountId]?.missing[kind] : undefined));
  const [refreshing, setRefreshing] = useState(false);
  const refresh = useCallback(() => {
    if (!accountId) return;
    setRefreshing(true);
    void session.loadPortfolio(accountId, kind, true).finally(() => setRefreshing(false));
  }, [session, accountId, kind]);
  return { accountId, snapshot, missing, refreshing, refresh };
}
