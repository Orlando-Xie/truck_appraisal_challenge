import type { AppraisalResult } from "../types";
import { compactMoney, confidenceStyle, money, rangeSpread } from "../format";
import { Card, Chip, RangeBar } from "./Primitives";

/** The headline: what it is worth, how sure we are, and what it is. */
export function Verdict({ result }: { result: AppraisalResult }) {
  const tryRange = result.price_try;
  const eurRange = result.price_eur;
  const ident = result.identification;
  const band = result.pricing_basis?.market_baseline_eur ?? null;

  if (!tryRange || !eurRange) return null;

  const vehicle = ident
    ? [ident.make, ident.model_family, ident.model_variant].filter(Boolean).join(" ")
    : "Unidentified truck";
  const specBits = [
    ident?.generation_year_low && ident?.generation_year_high
      ? ident.generation_year_low === ident.generation_year_high
        ? `${ident.generation_year_low}`
        : `${ident.generation_year_low}–${ident.generation_year_high}`
      : null,
    ident?.axle_configuration || null,
    ident?.euro_class || null,
    ident?.estimated_power_hp ? `${ident.estimated_power_hp} hp` : null,
    ident?.cab_type || null,
  ].filter(Boolean);

  // The outer scale is the model's full market band, so the reported interval is
  // visibly a slice of the market rather than a bare number.
  const outerLow = Math.min(band?.low ?? eurRange.low, eurRange.low) * 0.97;
  const outerHigh = Math.max(band?.high ?? eurRange.high, eurRange.high) * 1.03;

  return (
    <Card
      tone={result.status === "partial" ? "warn" : "good"}
      right={
        <div className="flex flex-col items-end gap-1.5">
          <Chip className={confidenceStyle(result.confidence_label)}>
            {result.confidence_label} confidence
          </Chip>
          <span className="text-[10px] text-ink-400">
            {result.elapsed_seconds}s{result.cache_hit ? " · cached" : ""}
          </span>
        </div>
      }
      title="Estimated market value"
      subtitle={`${vehicle}${specBits.length ? " · " + specBits.join(" · ") : ""}`}
    >
      <div className="flex flex-wrap items-end gap-x-10 gap-y-4">
        <div>
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-4xl font-semibold tracking-tight text-white tabular-nums">
              {money(tryRange.low, "TRY")}
            </span>
            <span className="text-2xl text-ink-400">–</span>
            <span className="text-4xl font-semibold tracking-tight text-white tabular-nums">
              {money(tryRange.high, "TRY")}
            </span>
          </div>
          <p className="mt-1.5 text-sm text-ink-300">
            Most likely <span className="font-medium text-ink-100">{money(tryRange.mid, "TRY")}</span>
            <span className="text-ink-400"> · {rangeSpread(tryRange)} spread</span>
          </p>
        </div>
        <div className="text-sm text-ink-300">
          <div className="text-[11px] uppercase tracking-wider text-ink-400">In euro</div>
          <div className="mt-0.5 tabular-nums">
            {money(eurRange.low, "EUR")} – {money(eurRange.high, "EUR")}
          </div>
        </div>
      </div>

      {band && (
        <div className="mt-5">
          <div className="mb-1.5 flex items-center justify-between text-[11px] text-ink-400">
            <span>
              This truck, inside the market band for its specification
              <span className="text-ink-500">
                {" "}
                ({compactMoney(band.low, "EUR")}–{compactMoney(band.high, "EUR")})
              </span>
            </span>
          </div>
          <RangeBar
            low={eurRange.low}
            high={eurRange.high}
            mid={eurRange.mid}
            outerLow={outerLow}
            outerHigh={outerHigh}
            format={(v) => compactMoney(v, "EUR")}
          />
        </div>
      )}

      {result.scenarios.length > 0 && (
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {result.scenarios.map((s) => (
            <div key={s.label} className="rounded-xl border border-ink-700 bg-ink-850/70 p-3">
              <div className="text-xs font-medium text-ink-100">{s.label}</div>
              <div className="mt-1 text-lg font-semibold tabular-nums text-white">
                {money(s.price_try.low, "TRY")} – {money(s.price_try.high, "TRY")}
              </div>
              <div className="mt-1 text-[11px] text-ink-400">{s.assumption}</div>
            </div>
          ))}
        </div>
      )}

      {result.seller_claims?.asking_price_try ? (
        <AskingPriceVerdict
          asking={Number(result.seller_claims.asking_price_try)}
          low={tryRange.low}
          high={tryRange.high}
        />
      ) : null}
    </Card>
  );
}

function AskingPriceVerdict({
  asking,
  low,
  high,
}: {
  asking: number;
  low: number;
  high: number;
}) {
  let verdict: { text: string; tone: string };
  if (asking < low) {
    const under = Math.round(((low - asking) / low) * 100);
    verdict = {
      text: `The asking price of ${money(asking, "TRY")} is about ${under}% below this range. Either it is a good buy or there is something the photos do not show.`,
      tone: "border-accent-500/30 bg-accent-500/10 text-accent-200",
    };
  } else if (asking > high) {
    const over = Math.round(((asking - high) / high) * 100);
    verdict = {
      text: `The asking price of ${money(asking, "TRY")} is about ${over}% above this range. Worth negotiating, or asking what justifies it.`,
      tone: "border-orange-500/30 bg-orange-500/10 text-orange-200",
    };
  } else {
    verdict = {
      text: `The asking price of ${money(asking, "TRY")} sits inside this range, so it is priced in line with the market.`,
      tone: "border-accent-500/30 bg-accent-500/10 text-accent-200",
    };
  }
  return <div className={`mt-5 rounded-xl border p-3 text-sm ${verdict.tone}`}>{verdict.text}</div>;
}
