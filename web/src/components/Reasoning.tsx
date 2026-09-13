import { useState } from "react";
import type { AppraisalResult } from "../types";
import { compactMoney, km as fmtKm, money, titleCase } from "../format";
import { Card, Chip, Empty } from "./Primitives";

/** What the price is actually compared against. */
export function Comparables({ result }: { result: AppraisalResult }) {
  const comps = result.comparables;
  const stats = result.pricing_basis;
  if (comps.length === 0) {
    return (
      <Card title="Comparable listings">
        <Empty>
          No genuinely comparable listings were found for this specification, which is itself a
          reason the range is wide.
        </Empty>
      </Card>
    );
  }

  return (
    <Card
      title="What this is priced against"
      subtitle={`${comps.length} real listings from the corpus, closest first`}
      right={
        stats ? (
          <Chip className="border-ink-600 bg-ink-800 text-ink-300">
            {stats.comparable_count} matched
          </Chip>
        ) : null
      }
    >
      <ul className="space-y-2">
        {comps.map((c) => (
          <li
            key={c.listing_id}
            className="flex gap-3 rounded-xl border border-ink-700 bg-ink-850/60 p-2.5"
          >
            {c.image_path ? (
              <img
                src={`/api/asset/image/${c.image_path.replace(/^images\//, "")}`}
                alt={c.title}
                loading="lazy"
                className="h-16 w-24 shrink-0 rounded-lg border border-ink-600 object-cover"
              />
            ) : (
              <div className="flex h-16 w-24 shrink-0 items-center justify-center rounded-lg border border-ink-700 bg-ink-800 text-[10px] text-ink-500">
                no photo
              </div>
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-3">
                <a
                  href={c.url || undefined}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="truncate text-sm font-medium text-ink-100 hover:text-accent-300"
                  title={c.title}
                >
                  {c.title || `${c.make} ${c.model_family}`}
                </a>
                <span className="shrink-0 text-sm font-semibold tabular-nums text-ink-100">
                  {c.price_eur ? money(c.price_eur, "EUR") : "—"}
                </span>
              </div>
              <div className="mt-0.5 text-[11px] text-ink-400">
                {[c.year, c.km ? fmtKm(c.km) : null, c.country].filter(Boolean).join(" · ")}
              </div>
              {c.match_reasons.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {c.match_reasons.map((r, i) => (
                    <Chip key={i} className="border-ink-600 bg-ink-800/70 text-ink-400">
                      {r}
                    </Chip>
                  ))}
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** The arithmetic, laid out so any single step can be argued with. */
export function PricingBasisPanel({ result }: { result: AppraisalResult }) {
  const basis = result.pricing_basis;
  const [showAll, setShowAll] = useState(false);
  if (!basis) return null;

  const band = basis.market_baseline_eur;
  const positioning = (basis.spec_used as any)?.positioning ?? {};
  const spec = basis.spec_used as Record<string, unknown>;

  const specRows = ["make", "model_family", "model_variant", "year", "km", "axle_config", "euro_class", "power_hp", "body_type"]
    .map((key) => [key, spec[key]] as [string, unknown])
    .filter(([, v]) => v != null && v !== "");

  return (
    <Card
      title="How this number was reached"
      subtitle={basis.model_used}
      right={
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="rounded-lg border border-ink-600 px-2.5 py-1 text-[11px] text-ink-300 transition hover:border-ink-400 hover:text-ink-100"
        >
          {showAll ? "Hide detail" : "Show detail"}
        </button>
      }
    >
      <ol className="space-y-2.5">
        <Step
          n={1}
          title="Market band for this specification"
          value={band ? `${compactMoney(band.low, "EUR")} – ${compactMoney(band.high, "EUR")}` : "—"}
          detail={
            "Gradient-boosted quantile regression over the scraped corpus. The band is wide because " +
            "identically specified trucks are advertised at very different prices depending on condition."
          }
        />
        {basis.interval_widening_pct > 0 && (
          <Step
            n={2}
            title="Widened for what the photos could not establish"
            value={`+${basis.interval_widening_pct}%`}
            tone="warn"
            detail={basis.widening_reasons.join(" ")}
          />
        )}
        <Step
          n={basis.interval_widening_pct > 0 ? 3 : 2}
          title="Positioned inside the band by visible condition"
          value={
            positioning.condition_index != null
              ? `${Math.round((positioning.centred_at_pct_of_band ?? 50))}% of the way up the band`
              : "middle of the band"
          }
          tone="good"
          detail={
            positioning.tier
              ? `Condition evidence rated "${positioning.tier}" (${Math.round((positioning.evidence_ratio ?? 0) * 100)}% of the positioning checklist was visible), so the reported interval is ${Math.round((positioning.width_fraction ?? 1) * 100)}% as wide as the full market band.`
              : undefined
          }
        />
        {basis.total_deductions_eur > 0 && (
          <Step
            n={basis.interval_widening_pct > 0 ? 4 : 3}
            title="Less the repair bills a buyer inherits"
            value={`−${money(basis.total_deductions_eur, "EUR")}`}
            tone="alert"
            detail={`${basis.deductions.length} itemised line${basis.deductions.length === 1 ? "" : "s"}, listed below.`}
          />
        )}
        <Step
          n={
            (basis.interval_widening_pct > 0 ? 1 : 0) + (basis.total_deductions_eur > 0 ? 1 : 0) + 3
          }
          title="Converted to Turkish lira"
          value={`×${basis.turkiye_multiplier} market premium, ×${basis.eur_try_rate} EUR/TRY`}
          detail={
            "Turkish asking prices sit above European ones for the same truck because of import duty, " +
            "OTV and KDV, and tighter supply of clean used tractors. Both factors are shown rather than hidden."
          }
        />
      </ol>

      {basis.deductions.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 text-[11px] uppercase tracking-wider text-ink-400">
            Itemised deductions
          </div>
          <ul className="divide-y divide-ink-700/70 overflow-hidden rounded-xl border border-ink-700">
            {basis.deductions.map((d) => (
              <li key={d.item_id} className="flex items-start gap-3 bg-ink-850/50 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="text-sm text-ink-100">
                    {d.label}
                    <span className="ml-2 text-[11px] text-ink-400">{d.severity}</span>
                  </div>
                  <div className="mt-0.5 text-[11px] leading-relaxed text-ink-400">
                    {d.rationale}
                  </div>
                </div>
                <span className="shrink-0 text-sm font-medium tabular-nums text-orange-200">
                  −{money(d.amount_eur, "EUR")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {showAll && (
        <div className="mt-4 space-y-4">
          <div>
            <div className="mb-2 text-[11px] uppercase tracking-wider text-ink-400">
              Specification priced
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
              {specRows.map(([k, v]) => (
                <div key={k}>
                  <dt className="text-[10px] uppercase tracking-wider text-ink-500">
                    {titleCase(k)}
                  </dt>
                  <dd className="text-xs text-ink-200">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </div>
          {basis.notes.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] uppercase tracking-wider text-ink-400">Notes</div>
              <ul className="space-y-1 text-xs leading-relaxed text-ink-300">
                {basis.notes.map((n, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="text-ink-500">·</span>
                    <span>{n}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function Step({
  n,
  title,
  value,
  detail,
  tone = "default",
}: {
  n: number;
  title: string;
  value: string;
  detail?: string;
  tone?: "default" | "warn" | "alert" | "good";
}) {
  const valueTone = {
    default: "text-ink-100",
    warn: "text-yellow-200",
    alert: "text-orange-200",
    good: "text-accent-300",
  }[tone];
  return (
    <li className="flex gap-3">
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-ink-600 text-[10px] text-ink-400">
        {n}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="text-sm text-ink-200">{title}</span>
          <span className={`text-sm font-medium tabular-nums ${valueTone}`}>{value}</span>
        </div>
        {detail && <p className="mt-0.5 text-[11px] leading-relaxed text-ink-400">{detail}</p>}
      </div>
    </li>
  );
}

/** Everything the system is explicit about not knowing. */
export function LimitsPanel({ result }: { result: AppraisalResult }) {
  const hasContent =
    result.missing_views.length > 0 ||
    result.blind_spots.length > 0 ||
    result.request_photos.length > 0 ||
    result.warnings.length > 0;
  if (!hasContent) return null;

  return (
    <Card
      tone="warn"
      title="What these photos could not tell me"
      subtitle="Stated rather than glossed over, because it is why the range is the width it is"
    >
      {result.request_photos.length > 0 && (
        <div className="mb-4 rounded-xl border border-accent-500/25 bg-accent-500/8 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-accent-300">
            Send these and the estimate gets tighter
          </div>
          <ul className="mt-1.5 space-y-1 text-sm text-ink-200">
            {result.request_photos.map((r, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-accent-400">→</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.missing_views.length > 0 && (
        <ul className="space-y-2">
          {result.missing_views.map((mv) => (
            <li key={mv.view} className="flex items-start gap-3">
              <Chip className="mt-0.5 shrink-0 border-yellow-500/30 bg-yellow-500/10 text-yellow-200">
                +{mv.widening_pct}%
              </Chip>
              <span className="text-sm leading-relaxed text-ink-300">{mv.why_it_matters}</span>
            </li>
          ))}
        </ul>
      )}

      {result.blind_spots.length > 0 && (
        <div className="mt-4">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-ink-400">
            Not covered by the photo set
          </div>
          <ul className="space-y-1 text-xs text-ink-400">
            {[...new Set(result.blind_spots)].slice(0, 8).map((b, i) => (
              <li key={i}>· {b}</li>
            ))}
          </ul>
        </div>
      )}

      {result.warnings.length > 0 && (
        <div className="mt-4 space-y-1.5">
          {result.warnings.map((w, i) => (
            <p key={i} className="text-xs text-yellow-200/90">
              ⚠ {w}
            </p>
          ))}
        </div>
      )}
    </Card>
  );
}

/** Identification, with the cues that support it. */
export function IdentificationPanel({ result }: { result: AppraisalResult }) {
  const ident = result.identification;
  if (!ident) return null;
  return (
    <Card
      title="Identification"
      subtitle="Read from the photos, with the cues used"
      right={
        <div className="flex gap-1.5">
          <Chip className="border-ink-600 bg-ink-800 text-ink-300">
            make {Math.round(ident.make_confidence * 100)}%
          </Chip>
          <Chip className="border-ink-600 bg-ink-800 text-ink-300">
            model {Math.round(ident.model_confidence * 100)}%
          </Chip>
        </div>
      }
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-4">
        {[
          ["Make", ident.make],
          ["Model", [ident.model_family, ident.model_variant].filter(Boolean).join(" ")],
          ["Generation", ident.generation],
          [
            "Built",
            ident.generation_year_low && ident.generation_year_high
              ? `${ident.generation_year_low}–${ident.generation_year_high}`
              : "",
          ],
          ["Cab", ident.cab_type],
          ["Axles", ident.axle_configuration],
          ["Power", ident.estimated_power_hp ? `${ident.estimated_power_hp} hp` : ""],
          ["Emissions", ident.euro_class],
          ["Body", ident.body_type ? titleCase(ident.body_type) : ""],
          ["Colour", ident.color],
        ]
          .filter(([, v]) => v)
          .map(([label, value]) => (
            <div key={label as string}>
              <dt className="text-[10px] uppercase tracking-wider text-ink-500">{label}</dt>
              <dd className="text-sm text-ink-100">{value}</dd>
            </div>
          ))}
      </dl>

      {ident.year_evidence && (
        <p className="mt-3 text-xs leading-relaxed text-ink-400">
          <span className="text-ink-300">Generation pinned by:</span> {ident.year_evidence}
        </p>
      )}

      {ident.identifying_evidence.length > 0 && (
        <div className="mt-3">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-ink-400">
            Visual cues used
          </div>
          <ul className="space-y-1 text-xs leading-relaxed text-ink-300">
            {ident.identifying_evidence.slice(0, 6).map((e, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-accent-400">✓</span>
                <span>{e}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {ident.alternative_hypotheses.length > 0 && (
        <div className="mt-3">
          <div className="mb-1.5 text-[11px] uppercase tracking-wider text-ink-400">
            Could also be
          </div>
          <div className="flex flex-wrap gap-1.5">
            {ident.alternative_hypotheses.map((a, i) => (
              <Chip key={i} className="border-ink-600 bg-ink-800/70 text-ink-300">
                {a.description} · {Math.round(a.probability * 100)}%
              </Chip>
            ))}
          </div>
        </div>
      )}

      {ident.contradicting_evidence.length > 0 && (
        <div className="mt-3 rounded-xl border border-yellow-500/25 bg-yellow-500/8 p-3">
          <div className="text-[11px] uppercase tracking-wider text-yellow-200">
            Does not quite fit
          </div>
          <ul className="mt-1 space-y-1 text-xs text-yellow-100/90">
            {ident.contradicting_evidence.map((c, i) => (
              <li key={i}>· {c}</li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

/** Contradictions against seller-typed details, and stolen-photo matches. */
export function IntegrityPanel({ result }: { result: AppraisalResult }) {
  const others = result.contradictions.filter((c) => c.field !== "km" && c.severity !== "note");
  const dupes = result.duplicate_photos;
  if (others.length === 0 && dupes.length === 0) return null;

  return (
    <Card tone="alert" title="Worth questioning">
      <div className="space-y-2.5">
        {others.map((c, i) => (
          <div key={i} className="rounded-xl border border-orange-500/30 bg-orange-500/8 p-3">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-semibold uppercase tracking-wider text-orange-200">
                {titleCase(c.field)}
              </span>
              {c.claimed && (
                <span className="text-ink-300">
                  seller says <span className="text-ink-100">{c.claimed}</span>
                </span>
              )}
              {c.observed && (
                <span className="text-ink-300">
                  photos say <span className="text-ink-100">{c.observed}</span>
                </span>
              )}
            </div>
            <p className="mt-1.5 text-sm leading-relaxed text-orange-100/90">{c.detail}</p>
          </div>
        ))}

        {dupes.map((d, i) => (
          <div key={i} className="rounded-xl border border-red-500/30 bg-red-500/8 p-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-red-200">
              These photos already exist in another advert
            </div>
            <p className="mt-1.5 text-sm leading-relaxed text-red-100/90">
              <span className="font-mono text-xs">{d.uploaded_filename}</span> matches{" "}
              {d.url ? (
                <a
                  href={d.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="underline hover:text-white"
                >
                  {d.title || d.listing_id}
                </a>
              ) : (
                <span>{d.title || d.listing_id}</span>
              )}
              {d.price_eur ? `, advertised at ${money(d.price_eur, "EUR")}` : ""} (perceptual hash
              distance {d.hamming_distance}). Either the seller is reusing someone else's photos, or
              this is the same truck relisted.
            </p>
          </div>
        ))}
      </div>
    </Card>
  );
}
