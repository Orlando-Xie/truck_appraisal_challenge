import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  appraiseStreaming,
  downscale,
  fetchInfo,
  fetchSamples,
} from "./api";
import type { AppraisalResult, PipelineInfo, SampleCase, TriagedImage } from "./types";
import { EMPTY_FORM, StageProgress, Uploader, type SellerForm } from "./components/Uploader";
import { Verdict } from "./components/Verdict";
import { ConditionPanel, MileagePanel } from "./components/ConditionPanel";
import {
  Comparables,
  IdentificationPanel,
  IntegrityPanel,
  LimitsPanel,
  PricingBasisPanel,
} from "./components/Reasoning";
import { Card, Chip } from "./components/Primitives";

export default function App() {
  const [info, setInfo] = useState<PipelineInfo | null>(null);
  const [samples, setSamples] = useState<SampleCase[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [form, setForm] = useState<SellerForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [stageMessages, setStageMessages] = useState<Record<string, string>>({});
  const [liveTriage, setLiveTriage] = useState<TriagedImage[]>([]);
  const [result, setResult] = useState<AppraisalResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchInfo().then(setInfo).catch(() => setInfo(null));
    fetchSamples().then(setSamples).catch(() => setSamples([]));
  }, []);

  // Object URLs keyed by filename, so evidence boxes can be cropped from the
  // exact photo they refer to.
  const photoUrls = useMemo(() => {
    const map: Record<string, string> = {};
    files.forEach((f) => {
      map[f.name] = URL.createObjectURL(f);
    });
    return map;
  }, [files]);

  useEffect(() => {
    return () => Object.values(photoUrls).forEach((url) => URL.revokeObjectURL(url));
  }, [photoUrls]);

  const samplePhotoUrls = useMemo(() => {
    if (!result || files.length > 0) return {};
    const map: Record<string, string> = {};
    result.images.forEach((im) => {
      map[im.filename] = `/api/asset/sample/${im.filename}`;
    });
    return map;
  }, [result, files.length]);

  const effectivePhotoUrls = files.length > 0 ? photoUrls : samplePhotoUrls;

  const reset = useCallback(() => {
    setFiles([]);
    setForm(EMPTY_FORM);
    setResult(null);
    setError(null);
    setStage(null);
    setStageMessages({});
    setLiveTriage([]);
  }, []);

  const run = useCallback(async () => {
    if (files.length === 0) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setStage(null);
    setStageMessages({});
    setLiveTriage([]);
    try {
      const prepared = await Promise.all(files.map((f) => downscale(f)));
      const res = await appraiseStreaming(prepared, form, (event) => {
        setStage(event.stage);
        setStageMessages((prev) => ({ ...prev, [event.stage]: event.message }));
        const images = (event.data as any)?.images;
        if (Array.isArray(images)) {
          // Show the per-photo classification the moment triage lands.
          setLiveTriage(
            images.map((im: any) => ({
              filename: im.filename,
              quality: { usable: im.usable, blur_score: im.blur_score } as any,
              triage: { subject: im.subject, view: im.view } as any,
            })),
          );
        }
      });
      setResult(res);
      setLiveTriage(res.images);
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setStage("done");
    }
  }, [files, form]);

  const runSample = useCallback(async (id: string) => {
    const sample = samples.find((s) => s.id === id);
    if (!sample) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setStage(null);
    setStageMessages({});
    setLiveTriage([]);
    try {
      const loaded: File[] = [];
      for (const rel of sample.files) {
        const res = await fetch(`/api/asset/sample/${encodeURIComponent(rel)}`);
        if (!res.ok) continue;
        const blob = await res.blob();
        loaded.push(new File([blob], rel.split("/").pop() || rel, { type: blob.type || "image/jpeg" }));
      }
      setFiles(loaded);
      const claims = (sample.claims || {}) as Record<string, unknown>;
      const details = {
        year: claims.year != null ? String(claims.year) : "",
        make: claims.make != null ? String(claims.make) : "",
        model: claims.model != null ? String(claims.model) : "",
        km: claims.km != null ? String(claims.km) : "",
        asking_price_try: claims.asking_price_try != null ? String(claims.asking_price_try) : "",
      };
      setForm({ ...EMPTY_FORM, ...details });
      const res = await appraiseStreaming(loaded, details, (event) => {
        setStage(event.stage);
        setStageMessages((prev) => ({ ...prev, [event.stage]: event.message }));
        const images = (event.data as any)?.images;
        if (Array.isArray(images)) {
          setLiveTriage(
            images.map((im: any) => ({
              filename: im.filename,
              quality: { usable: im.usable, blur_score: im.blur_score } as any,
              triage: { subject: im.subject, view: im.view } as any,
            })),
          );
        }
      });
      setResult(res);
      setLiveTriage(res.images);
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setStage("done");
    }
  }, [samples]);

  const rubric = info?.rubric_items ?? [];

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-12">
      <Header info={info} />

      <div className="mt-8 space-y-4">
        <Uploader
          files={files}
          setFiles={setFiles}
          form={form}
          setForm={setForm}
          onSubmit={run}
          onReset={reset}
          busy={busy}
          info={info}
          samples={samples}
          onSample={runSample}
          triaged={liveTriage}
          photoUrls={photoUrls}
        />

        <StageProgress
          stages={info?.stages ?? DEFAULT_STAGES}
          current={stage}
          messages={stageMessages}
          busy={busy}
        />

        {error && (
          <Card tone="alert" title="Something went wrong">
            <p className="text-sm text-red-200">{error}</p>
          </Card>
        )}
      </div>

      {result && (
        <div ref={resultRef} className="mt-6 space-y-4">
          {result.refusal ? (
            <RefusalCard result={result} />
          ) : (
            <>
              <Verdict result={result} />
              <IntegrityPanel result={result} />
              <MileagePanel result={result} />
              <ConditionPanel result={result} rubric={rubric} photoUrls={effectivePhotoUrls} />
              <LimitsPanel result={result} />
              <IdentificationPanel result={result} />
              <PricingBasisPanel result={result} />
              <Comparables result={result} />
            </>
          )}
        </div>
      )}

      <Footer info={info} />
    </div>
  );
}

const DEFAULT_STAGES = [
  { id: "quality", label: "Checking photo quality" },
  { id: "triage", label: "Working out what these photos show" },
  { id: "gates", label: "Deciding whether this can be appraised" },
  { id: "identify", label: "Identifying the vehicle" },
  { id: "condition", label: "Inspecting condition" },
  { id: "comps", label: "Finding comparable listings" },
  { id: "price", label: "Pricing" },
  { id: "done", label: "Done" },
];

function Header({ info }: { info: PipelineInfo | null }) {
  const metrics = info?.price_model?.metrics_test;
  const stub = info?.vision?.provider === "stub";
  return (
    <header>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-3xl">
            What's this truck worth?
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-300">
            Upload photos of a used truck. You get a price range, a condition report you can check
            against the pixels, and a straight answer about what the photos could not tell me.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          {info?.price_model?.available ? (
            <Chip className="border-accent-500/30 bg-accent-500/10 text-accent-300">
              {info.price_model.n_train?.toLocaleString("en-GB")} listings trained
            </Chip>
          ) : (
            <Chip className="border-red-500/30 bg-red-500/10 text-red-200">no price model</Chip>
          )}
          {metrics?.median_ape != null && (
            <Chip className="border-ink-600 bg-ink-800 text-ink-300">
              {Math.round(metrics.median_ape * 100)}% median error on held-out listings
            </Chip>
          )}
        </div>
      </div>

      {stub && (
        <div className="mt-4 rounded-xl border border-yellow-500/30 bg-yellow-500/8 p-3 text-xs text-yellow-100">
          <span className="font-semibold">Running without a vision API key.</span> The pricing model,
          the abstention gates and the whole pipeline are live, but identification and condition are
          synthetic placeholders. Set <code className="font-mono">GEMINI_API_KEY</code> in{" "}
          <code className="font-mono">.env</code> for real photo analysis.
        </div>
      )}
    </header>
  );
}

function RefusalCard({ result }: { result: AppraisalResult }) {
  const refusal = result.refusal!;
  return (
    <Card tone="alert" title="I am not going to price this">
      <h3 className="text-lg font-semibold text-red-200">{refusal.headline}</h3>
      <p className="mt-2 text-sm leading-relaxed text-ink-200">{refusal.detail}</p>

      {refusal.what_to_send.length > 0 && (
        <div className="mt-4 rounded-xl border border-accent-500/25 bg-accent-500/8 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-accent-300">
            Send these instead
          </div>
          <ul className="mt-1.5 space-y-1 text-sm text-ink-200">
            {refusal.what_to_send.map((w, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-accent-400">→</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.images.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 text-[11px] uppercase tracking-wider text-ink-400">
            What each photo was judged to be
          </div>
          <ul className="space-y-1.5">
            {result.images.map((im) => (
              <li key={im.filename} className="flex flex-wrap items-baseline gap-2 text-xs">
                <span className="font-mono text-ink-400">{im.filename}</span>
                <span className="text-ink-200">
                  {im.triage.subject_description || im.triage.subject}
                </span>
                {im.quality.notes.length > 0 && (
                  <span className="text-red-300">— {im.quality.notes.join("; ")}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-4 text-xs leading-relaxed text-ink-400">
        Refusing is deliberate. A confident price on the wrong subject, or on photos nothing can be
        read from, is worse for a buyer than no price at all.
      </p>
    </Card>
  );
}

function Footer({ info }: { info: PipelineInfo | null }) {
  const cal = info?.calibration;
  return (
    <footer className="mt-12 border-t border-ink-800 pt-5 text-[11px] leading-relaxed text-ink-500">
      <p>
        Prices come from a gradient-boosted quantile regression over scraped listings, not from a
        language model. The vision model is only ever asked what it can see; it never produces a
        number.
      </p>
      {cal && (
        <p className="mt-1.5">
          Turkish lira conversion uses a ×{cal.turkiye_multiplier} market premium over European
          asking prices and a rate of {cal.eur_try} TRY per euro
          {cal.source === "default" ? " (not yet calibrated against Turkish listings)" : ` (calibrated on ${cal.n_listings} Turkish listings)`}
          .
        </p>
      )}
      <p className="mt-1.5">
        Estimates are asking-price guidance from photographs, not an inspection. Anything mechanical,
        structural or documentary needs a physical check.
      </p>
    </footer>
  );
}
