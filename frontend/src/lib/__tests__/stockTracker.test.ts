import { describe, expect, it } from "vitest";
import {
  buildBacktestPayload,
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
