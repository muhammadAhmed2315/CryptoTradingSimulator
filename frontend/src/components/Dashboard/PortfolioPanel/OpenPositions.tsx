import { QueryClient, useQuery } from "@tanstack/react-query";

import { Card } from "@/components/ui/card";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionPanel,
} from "@/components/animate-ui/components/base/accordion";
import CustomSkeleton from "@/components/CustomSkeleton";
import { numToMoney } from "@/utils";
import HoldingsBreakdownBar from "./HoldingsBreakdownBar";
import { Separator } from "@/components/ui/separator";
import OpenOrderRow from "./OpenOrderRow";
import { fetchWithRefresh, API_BASE } from "@/lib/api";
import ErrorFallback from "@/components/ErrorFallback";

// ===== NAVBAR PREFETCH =====
export function prefetchOpenPositions(queryClient: QueryClient) {
  return Promise.all([
    queryClient.prefetchQuery({
      queryKey: ["openTrades"],
      queryFn: getOpenTrades,
    }),
  ]);
}

// ===== API FUNCTIONS =====
async function getOpenTrades() {
  const response = await fetchWithRefresh(`${API_BASE}/get_open_trades`, {
    method: "get",
    credentials: "include",
  });

  if (!response.ok) throw await response.json();

  return await response.json();
}

// ===== HELPER FUNCTIONS =====
function filterOrdersByType(
  tradesData: any,
  orderType: string,
  transactionType: string,
) {
  return tradesData.filter(
    (order: any) =>
      order.order_type === orderType &&
      order.transaction_type === transactionType,
  );
}

function sumOrderValues(trades: any) {
  return trades.reduce(
    (acc: number, order: any) => acc + order.current_price * order.quantity,
    0,
  );
}

export default function OpenPositions() {
  // ===== REACT QUERY HOOKS =====
  const openTradesQuery = useQuery({
    queryKey: ["openTrades"],
    queryFn: getOpenTrades,
  });

  // ===== DERIVED STATE =====
  const reservedValue = openTradesQuery.data
    ? openTradesQuery.data.reduce(
        (acc: number, order: any) => acc + order.current_price * order.quantity,
        0,
      )
    : 0;

  const openOrdersByType = openTradesQuery.data
    ? {
        "limit buy": filterOrdersByType(openTradesQuery.data, "limit", "buy"),
        "limit sell": filterOrdersByType(openTradesQuery.data, "limit", "sell"),
        "stop buy": filterOrdersByType(openTradesQuery.data, "stop", "buy"),
        "stop sell": filterOrdersByType(openTradesQuery.data, "stop", "sell"),
      }
    : {};

  const openOrdersSummary = openTradesQuery.data
    ? Object.keys(openOrdersByType).map((key) => ({
        id: key,
        ticker: key,
        totalValue: sumOrderValues(
          openOrdersByType[key as keyof typeof openOrdersByType],
        ),
        orderType: key.split(" ")[0],
        transactionType: key.split(" ")[1],
      }))
    : [];

  const openOrdersSummarySorted = [...openOrdersSummary].sort(
    (a, b) => b.totalValue - a.totalValue,
  );

  const orderTypesWithMoreThanZero = Object.entries(openOrdersByType).reduce<
    string[]
  >((acc, [key, value]) => {
    if (value.length > 0) acc.push(key);
    return acc;
  }, []);

  if (openTradesQuery.isError) {
    return (
      <Card className="gap-0 p-0 min-h-120 flex items-center justify-center">
        <ErrorFallback
          title="Open positions unavailable"
          description="Open orders could not be loaded."
        />
      </Card>
    );
  }

  return (
    <Card className="gap-0 p-0">
      {/* ===== HEADER ===== */}
      <div className="p-5 pb-0">
        {/* ===== RESERVED VALUE ===== */}
        <p className="text-xs text-muted-foreground font-mono">
          RESERVED VALUE
        </p>
        {openTradesQuery.isLoading && (
          <CustomSkeleton className="h-10 w-full mt-2 mb-4" />
        )}
        {openTradesQuery.data && (
          <h1 className="text-2xl font-bold mb-2">
            ${numToMoney(reservedValue)}
          </h1>
        )}

        {/* ===== BREAKDOWN BAR ===== */}
        {openTradesQuery.data &&
          openOrdersSummarySorted.reduce(
            (acc, order) => acc + order.totalValue,
            0,
          ) !== 0 && (
            <div className="pb-2">
              <HoldingsBreakdownBar
                holdings={openOrdersSummarySorted.map((order) => {
                  return {
                    id: order.id,
                    totalValue: order.totalValue,
                    ticker: order.ticker,
                  };
                })}
              />
            </div>
          )}
      </div>

      <Separator />

      <div className="p-5 pt-0">
        {/* ===== LOADING STATE ===== */}
        {openTradesQuery.isLoading &&
          Array.from({ length: 4 }, (_, i) => (
            <div
              key={i}
              className="flex items-start gap-2 py-4 border-b last:border-b-0"
            >
              <CustomSkeleton className="size-4 shrink-0 rounded-sm translate-y-0.5" />
              <div className="flex justify-between items-center w-full">
                <CustomSkeleton className="h-4 w-32" />
                <CustomSkeleton className="h-3.5 w-16" />
              </div>
            </div>
          ))}

        {openTradesQuery.data && (
          <Accordion multiple={true} defaultValue={orderTypesWithMoreThanZero}>
            {openOrdersSummary.map((orders) => {
              return (
                <AccordionItem key={orders.id} value={orders.id}>
                  {/* ===== TRIGGER ===== */}
                  <AccordionTrigger className="hover:no-underline cursor-pointer py-3">
                    <div className="flex justify-between w-full">
                      <span className="cursor-pointer font-bold text-[15px] capitalize">
                        {orders.id} Orders
                      </span>
                      <span className="font-mono text-[13px] text-muted-foreground pt-0">
                        {
                          openOrdersByType[
                            orders.id as keyof typeof openOrdersByType
                          ].length
                        }{" "}
                        ORDERS
                      </span>
                    </div>
                  </AccordionTrigger>

                  {/* ===== CONTENT ===== */}
                  <AccordionPanel className="flex-col pb-1">
                    {/* ===== ORDERS EXIST ===== */}
                    {openOrdersByType[
                      orders.id as keyof typeof openOrdersByType
                    ].length > 0 &&
                      openOrdersByType[
                        orders.id as keyof typeof openOrdersByType
                      ].map((order: any) => (
                        <OpenOrderRow
                          key={order.id}
                          order={order}
                          refetch={openTradesQuery.refetch}
                        />
                      ))}

                    {/* ===== NO ORDERS ===== */}
                    {openOrdersByType[
                      orders.id as keyof typeof openOrdersByType
                    ].length === 0 && (
                      <div className="flex items-center gap-2 px-3 py-2 bg-muted rounded-lg mb-1.5">
                        <p className="font-mono text-base text-muted-foreground/70 w-7 h-7 flex items-center justify-center bg-background border border-border rounded-full shrink-0">
                          0
                        </p>

                        <div className="flex flex-col gap-0 min-w-0">
                          <p className="text-base font-semibold text-foreground">
                            No {orders.id} orders placed
                          </p>
                          <p className="text-sm text-muted-foreground">
                            {orders.id === "limit buy"
                              ? "No bids waiting below market."
                              : orders.id === "limit sell"
                                ? "No asks waiting above market."
                                : orders.id === "stop buy"
                                  ? "Nothing waiting above market right now."
                                  : "Nothing waiting below market right now."}
                          </p>
                        </div>
                      </div>
                    )}
                  </AccordionPanel>
                </AccordionItem>
              );
            })}
          </Accordion>
        )}
      </div>
    </Card>
  );
}
