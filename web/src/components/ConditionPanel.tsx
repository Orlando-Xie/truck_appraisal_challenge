import type { AppraisalResult, RubricItem } from "../types";
import { SEVERITY_STYLE, km as fmtKm, money, titleCase } from "../format";
import { Card, Chip, Empty } from "./Primitives";
import { EvidenceCrop } from "./EvidenceCrop";

const ROLE_HINT: Record<string, string> = {
  positioning: "Sets where in the market band this truck sits",
  deduction: "Costed as a repair bill and taken off the price",
};

export function ConditionPanel({
  result,
  rubric,
  photoUrls,
}: {
  result: AppraisalResult;
  rubric: RubricItem[];
  photoUrls: Record<string, string>;
}) {
  const condition = result.condition;
  if (!condition) return null;

  const labelFor = (id: string) => rubric.find((r) => r.id === id)?.label ?? titleCase(id);
  const roleFor = (id: string) => rubric.find((r) => r.id === id)?.role ?? "deduction";
  const deductionById = new Map(
    (result.pricing_basis?.deductions ?? []).map((d) => [d.item_id, d]),
  );

  const ranked = [...condition.findings].sort((a, b) => {
    const order = { severe: 0, moderate: 1, minor: 2, none: 3, not_observable: 4 };
    return order[a.severity] - order[b.severity];
  });
  const flagged = ranked.filter((f) => ["severe", "moderate", "minor"].includes(f.severity));
  const clean = ranked.filter((f) => f.severity === "none");
  const unseen = ranked.filter((f) => f.severity === "not_observable");

  return (
    <Card
      title="Condition report"
      subtitle={`${flagged.length} item${flagged.length === 1 ? "" : "s"} flagged · ${clean.length} checked and sound · ${unseen.length} not visible in these photos`}
    >
      {condition.overall_impression && (
        <p className="mb-4 text-sm leading-relaxed text-ink-200">{condition.overall_impression}</p>
      )}

      {condition.roadworthy_concerns.length > 0 && (
        <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/8 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-red-200">
            Check before driving it away
          </div>
          <ul className="mt-1.5 space-y-1 text-sm text-red-100/90">
            {condition.roadworthy_concerns.map((c, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-red-400">•</span>
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <TyrePanel result={result} />

      {flagged.length === 0 ? (
        <Empty>Nothing visible in these photos was flagged as a defect.</Empty>
      ) : (
        <ul className="space-y-2.5">
          {flagged.map((finding) => {
            const style = SEVERITY_STYLE[finding.severity];
            const deduction = deductionById.get(finding.item_id);
            const role = roleFor(finding.item_id);
            return (
              <li
                key={finding.item_id}
                className="rounded-xl border border-ink-700 bg-ink-850/60 p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${style.dot}`} />
                  <span className="text-sm font-medium text-ink-100">
                    {labelFor(finding.item_id)}
                  </span>
                  <Chip className={style.chip}>{style.label}</Chip>
                  {deduction ? (
                    <Chip
                      className="border-ink-600 bg-ink-800 text-ink-200"
                      title={ROLE_HINT.deduction}
                    >
                      −{money(deduction.amount_eur, "EUR")}
                    </Chip>
                  ) : role === "positioning" ? (
                    <Chip className="border-ink-600 bg-ink-800/60 text-ink-400" title={ROLE_HINT.positioning}>
                      affects market position
                    </Chip>
                  ) : null}
                  {finding.confidence < 0.5 && (
                    <Chip className="border-ink-600 bg-ink-800/60 text-ink-400">
                      low confidence
                    </Chip>
                  )}
                </div>
                <div className="mt-2 flex gap-3">
                  {finding.evidence.length > 0 && (
                    <div className="flex gap-1.5">
                      {finding.evidence.slice(0, 3).map((box, i) => (
                        <EvidenceCrop key={i} box={box} src={photoUrls[box.filename]} />
                      ))}
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm leading-relaxed text-ink-300">{finding.observation}</p>
                    {deduction && deduction.rationale !== finding.observation && (
                      <p className="mt-1 text-xs text-ink-400">{deduction.rationale}</p>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {(clean.length > 0 || unseen.length > 0) && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {clean.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] uppercase tracking-wider text-ink-400">
                Checked and sound
              </div>
              <div className="flex flex-wrap gap-1.5">
                {clean.map((f) => (
                  <Chip key={f.item_id} className={SEVERITY_STYLE.none.chip}>
                    {labelFor(f.item_id)}
                  </Chip>
                ))}
              </div>
            </div>
          )}
          {unseen.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] uppercase tracking-wider text-ink-400">
                Not visible — not assumed good
              </div>
              <div className="flex flex-wrap gap-1.5">
                {unseen.map((f) => (
                  <Chip key={f.item_id} className={SEVERITY_STYLE.not_observable.chip}>
                    {labelFor(f.item_id)}
                  </Chip>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function TyrePanel({ result }: { result: AppraisalResult }) {
  const tires = result.condition?.tires;
  if (!tires) return null;

  if (!tires.tires_assessable || tires.tread_remaining_pct < 0) {
    return (
      <div className="mb-4 rounded-xl border border-ink-700 bg-ink-850/60 p-3 text-sm text-ink-300">
        <span className="font-medium text-ink-200">Tyres: not assessable.</span> No photo shows a
        tyre closely enough to judge tread. A full set on a tractor unit is a four-figure cost, so
        this is left out of the price rather than guessed — and it is part of why the range is as
        wide as it is.
      </div>
    );
  }

  const pct = tires.tread_remaining_pct;
  const tone =
    pct >= 60
      ? "border-accent-500/30 bg-accent-500/8"
      : pct >= 30
        ? "border-yellow-500/30 bg-yellow-500/8"
        : "border-red-500/30 bg-red-500/8";
  const flags = [
    tires.uneven_wear && "uneven wear",
    tires.mismatched_sizes_or_brands && "mismatched across an axle",
    tires.sidewall_damage_or_cracking && "sidewall damage",
    tires.retread_detected && "retreaded casings",
  ].filter(Boolean) as string[];

  return (
    <div className={`mb-4 rounded-xl border p-3 ${tone}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-ink-100">
          Tyres · roughly {pct}% tread remaining
        </span>
        <span className="text-[11px] text-ink-400">
          from {tires.tires_visible_count} visible tyre
          {tires.tires_visible_count === 1 ? "" : "s"}
        </span>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-ink-700">
        <div
          className={`h-2 rounded-full ${pct >= 60 ? "bg-accent-400" : pct >= 30 ? "bg-yellow-400" : "bg-red-400"}`}
          style={{ width: `${Math.max(3, Math.min(100, pct))}%` }}
        />
      </div>
      {flags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {flags.map((f) => (
            <Chip key={f} className="border-orange-500/30 bg-orange-500/10 text-orange-200">
              {f}
            </Chip>
          ))}
        </div>
      )}
      {tires.notes && <p className="mt-2 text-xs text-ink-300">{tires.notes}</p>}
    </div>
  );
}

/** The odometer-versus-wear cross-check, shown whenever there is anything to say. */
export function MileagePanel({ result }: { result: AppraisalResult }) {
  const wear = result.condition?.wear;
  if (!wear) return null;
  const alert = result.contradictions.find((c) => c.field === "km");
  const hasWear = wear.wear_implied_km_low > 0 && wear.wear_implied_km_high > 0;
  if (!wear.odometer_visible && !hasWear) return null;

  return (
    <Card
      tone={alert?.severity === "alert" ? "alert" : "default"
      }
      title="Mileage cross-check"
      subtitle="The odometer, against mileage estimated from wear alone"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-ink-700 bg-ink-850/60 p-3">
          <div className="text-[11px] uppercase tracking-wider text-ink-400">Odometer reads</div>
          <div className="mt-1 text-xl font-semibold tabular-nums text-ink-100">
            {wear.odometer_visible && wear.odometer_reading_km > 0
              ? fmtKm(wear.odometer_reading_km)
              : "Not visible"}
          </div>
          {wear.odometer_visible && (
            <div className="mt-1 text-[11px] text-ink-400">
              read with {Math.round(wear.odometer_confidence * 100)}% confidence
            </div>
          )}
        </div>
        <div className="rounded-xl border border-ink-700 bg-ink-850/60 p-3">
          <div className="text-[11px] uppercase tracking-wider text-ink-400">Wear suggests</div>
          <div className="mt-1 text-xl font-semibold tabular-nums text-ink-100">
            {hasWear
              ? `${fmtKm(wear.wear_implied_km_low)} – ${fmtKm(wear.wear_implied_km_high)}`
              : "Not assessable"}
          </div>
          {wear.wear_evidence.length > 0 && (
            <ul className="mt-1.5 space-y-0.5 text-[11px] text-ink-400">
              {wear.wear_evidence.slice(0, 4).map((e, i) => (
                <li key={i}>· {e}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {alert ? (
        <div
          className={`mt-4 rounded-xl border p-3 text-sm ${
            alert.severity === "alert"
              ? "border-red-500/35 bg-red-500/10 text-red-100"
              : "border-yellow-500/30 bg-yellow-500/8 text-yellow-100"
          }`}
        >
          <div className="font-semibold">
            {alert.severity === "alert"
              ? "The odometer and the wear do not agree"
              : "Mileage worth checking"}
          </div>
          <p className="mt-1 leading-relaxed">{alert.detail}</p>
        </div>
      ) : hasWear && wear.odometer_visible ? (
        <p className="mt-3 text-sm text-accent-200">
          The wear is consistent with the odometer, which is a point in the seller's favour.
        </p>
      ) : null}
    </Card>
  );
}
