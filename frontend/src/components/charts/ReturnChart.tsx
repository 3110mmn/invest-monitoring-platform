"use client";

import { ColorType, createChart, LineSeries } from "lightweight-charts";
import type { IChartApi, UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { MarketPrice } from "@/types";

function toReturns(prices: MarketPrice[]) {
  return prices
    .filter((price) => price.cumulative_return != null)
    .map((price) => ({
      time: (Date.parse(`${price.obs_date}T00:00:00Z`) / 1000) as UTCTimestamp,
      value: (price.cumulative_return as number) * 100,
    }));
}

export default function ReturnChart({ prices }: { prices: MarketPrice[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#374151" },
      grid: { vertLines: { color: "#f3f4f6" }, horzLines: { color: "#f3f4f6" } },
      rightPriceScale: { borderColor: "#e5e7eb" },
      timeScale: { borderColor: "#e5e7eb", timeVisible: false },
      height: 360,
      autoSize: true,
    });
    chartRef.current = chart;

    const returns = chart.addSeries(LineSeries, {
      color: "#2563eb",
      lineWidth: 2,
      priceFormat: { type: "custom", formatter: (value: number) => `${value.toFixed(1)}%` },
    });
    returns.setData(toReturns(prices));
    chart.timeScale().fitContent();

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [prices]);

  if (!prices.some((price) => price.cumulative_return != null)) {
    return <p className="text-gray-500 text-sm py-12 text-center">価格リターンを計算できません</p>;
  }
  return <div ref={containerRef} className="w-full" />;
}
