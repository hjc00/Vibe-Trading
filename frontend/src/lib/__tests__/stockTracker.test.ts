import { describe, expect, it } from "vitest";
import {
  buildBacktestPayload,
  isInAShareTradingSession,
  serializeBacktestRule,
  type BacktestStoredSettings,
} from "@/lib/stockTracker";

function stored(overrides: Partial<BacktestStoredSettings> = {}): BacktestStoredSettings {
  return {
    presetId: "ma_golden_cross",
    spec: {
      buy: {
        mode: "and",
        conditions: [
          { primitive: "fast_ma_above_slow", trigger: "edge_up", params: { fast: 5, slow: 20 } },
          { primitive: "close_above_ma", trigger: "state", params: { n: 20 }, enabled: false },
        ],
      },
      sell: { mode: "and", conditions: [] },
    },
    sellDisabled: false,
    multiBuys: true,
    takeProfitPct: "",
    stopLossPct: "",
    ...overrides,
  };
}

describe("buildBacktestPayload", () => {
  it("returns null when buy or sell conditions are absent", () => {
    expect(buildBacktestPayload(null)).toBeNull();
    expect(buildBacktestPayload(undefined)).toBeNull();
    // Guard fires only when the conditions arrays themselves are missing.
    expect(
      buildBacktestPayload(
        stored({
          spec: { buy: { mode: "and", conditions: [] }, sell: { mode: "and", conditions: undefined as never } },
        }),
      ),
    ).toBeNull();
  });

  it("drops disabled conditions and the UI-only enabled flag", () => {
    const payload = buildBacktestPayload(stored());
    expect(payload).not.toBeNull();
    expect(payload!.buy.conditions).toHaveLength(1);
    expect(payload!.buy.conditions[0]).toEqual({
      primitive: "fast_ma_above_slow",
      trigger: "edge_up",
      params: { fast: 5, slow: 20 },
    });
  });

  it("empty sell when sellDisabled is set", () => {
    const payload = buildBacktestPayload(stored({ sellDisabled: true }));
    expect(payload!.sell.conditions).toEqual([]);
  });

  it("converts take-profit / stop-loss percentages to decimals", () => {
    const payload = buildBacktestPayload(stored({ takeProfitPct: "8", stopLossPct: "5" }));
    expect(payload!.take_profit_pct).toBe(0.08);
    expect(payload!.stop_loss_pct).toBe(0.05);
  });

  it("ignores non-positive percentages", () => {
    const payload = buildBacktestPayload(stored({ takeProfitPct: "0", stopLossPct: "-3" }));
    expect(payload!.take_profit_pct).toBeUndefined();
    expect(payload!.stop_loss_pct).toBeUndefined();
  });

  it("maps multiBuys false to allow_multiple_buys false", () => {
    expect(buildBacktestPayload(stored({ multiBuys: false }))!.allow_multiple_buys).toBe(false);
    expect(buildBacktestPayload(stored({ multiBuys: true }))!.allow_multiple_buys).toBe(true);
  });
});

describe("serializeBacktestRule", () => {
  it("keeps enabled conditions and strips the enabled flag", () => {
    const rule = {
      mode: "and" as const,
      conditions: [
        { primitive: "a", trigger: "state" as const, params: {}, enabled: true },
        { primitive: "b", trigger: "edge_up" as const, params: {}, enabled: false },
      ],
    };
    const out = serializeBacktestRule(rule);
    expect(out.conditions).toHaveLength(1);
    expect(out.conditions[0].enabled).toBeUndefined();
  });
});

describe("isInAShareTradingSession", () => {
  // Shanghai is UTC+8 year-round (no DST), so Date.UTC(h - 8) maps to the
  // matching Shanghai wall time. 2024-01-03 was a Wednesday, 2024-01-06 a Saturday.
  const atShanghai = (iso: string) => new Date(iso);

  it("is true across the morning and afternoon sessions on weekdays", () => {
    expect(isInAShareTradingSession(atShanghai("2024-01-03T01:15:00Z"))).toBe(true); // 09:15 open
    expect(isInAShareTradingSession(atShanghai("2024-01-03T02:00:00Z"))).toBe(true); // 10:00
    expect(isInAShareTradingSession(atShanghai("2024-01-03T03:30:00Z"))).toBe(true); // 11:30 morning close
    expect(isInAShareTradingSession(atShanghai("2024-01-03T05:00:00Z"))).toBe(true); // 13:00 afternoon open
    expect(isInAShareTradingSession(atShanghai("2024-01-03T07:00:00Z"))).toBe(true); // 15:00 close
  });

  it("is false before open, at lunch, after close, and on weekends", () => {
    expect(isInAShareTradingSession(atShanghai("2024-01-03T01:14:00Z"))).toBe(false); // 09:14 pre-market
    expect(isInAShareTradingSession(atShanghai("2024-01-03T03:31:00Z"))).toBe(false); // 11:31 lunch start
    expect(isInAShareTradingSession(atShanghai("2024-01-03T04:59:00Z"))).toBe(false); // 12:59 lunch
    expect(isInAShareTradingSession(atShanghai("2024-01-03T07:01:00Z"))).toBe(false); // 15:01 after close
    expect(isInAShareTradingSession(atShanghai("2024-01-06T02:00:00Z"))).toBe(false); // Saturday 10:00
  });
});
