import { useCallback, useEffect, useRef, useState } from "react";
import type { PipelineInfo, SampleCase, TriagedImage } from "../types";
import { SUBJECT_LABELS, VIEW_LABELS } from "../format";
import { Chip } from "./Primitives";

export interface SellerForm {
  year: string;
  make: string;
  model: string;
  km: string;
  asking_price_try: string;
}

export const EMPTY_FORM: SellerForm = {
  year: "",
  make: "",
  model: "",
  km: "",
  asking_price_try: "",
};

export function Uploader({
  files,
  setFiles,
  form,
  setForm,
  onSubmit,
  onReset,
  busy,
  info,
  samples,
  onSample,
  triaged,
  photoUrls,
}: {
  files: File[];
  setFiles: (files: File[]) => void;
  form: SellerForm;
  setForm: (form: SellerForm) => void;
  onSubmit: () => void;
  onReset: () => void;
  busy: boolean;
  info: PipelineInfo | null;
  samples: SampleCase[];
  onSample: (id: string) => void;
  triaged: TriagedImage[];
  photoUrls: Record<string, string>;
}) {
  const [dragging, setDragging] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const maxImages = info?.max_images ?? 12;

  const addFiles = useCallback(
    (incoming: FileList | File[] | null) => {
      if (!incoming) return;
      const images = Array.from(incoming).filter((f) => f.type.startsWith("image/") || /\.(jpe?g|png|webp|heic|heif)$/i.test(f.name));
      if (images.length === 0) return;
      const merged = [...files];
      for (const f of images) {
        if (merged.length >= maxImages) break;
        if (!merged.some((m) => m.name === f.name && m.size === f.size)) merged.push(f);
      }
      setFiles(merged);
    },
    [files, maxImages, setFiles],
  );

  // Pasting a screenshot straight in is the fastest path during a live demo.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const items = e.clipboardData?.files;
      if (items && items.length) addFiles(items);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [addFiles]);

  const triageFor = (name: string) => triaged.find((t) => t.filename === name);

  return (
    <div className="space-y-4">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          addFiles(e.dataTransfer.files);
        }}
        className={`rounded-2xl border-2 border-dashed p-6 text-center transition ${
          dragging
            ? "border-accent-400 bg-accent-500/8"
            : "border-ink-600 bg-ink-900/50 hover:border-ink-400"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="image/*"
          className="hidden"
          onChange={(e) => addFiles(e.target.files)}
        />
        <p className="text-sm text-ink-200">
          Drop truck photos here, paste them, or{" "}
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="font-medium text-accent-300 underline decoration-accent-500/40 hover:text-accent-200"
          >
            browse
          </button>
        </p>
        <p className="mt-1.5 text-xs text-ink-400">
          Up to {maxImages} photos. Bad lighting, mud and awkward angles are expected — the system
          will say what it cannot see.
        </p>
      </div>

      {files.length > 0 && (
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6">
          {files.map((f) => {
            const t = triageFor(f.name);
            const url = photoUrls[f.name];
            const bad = t && !t.quality.usable;
            return (
              <div
                key={`${f.name}-${f.size}`}
                className={`group relative overflow-hidden rounded-xl border ${
                  bad ? "border-red-500/50" : "border-ink-700"
                }`}
              >
                {url ? (
                  <img src={url} alt={f.name} className="h-24 w-full object-cover" />
                ) : (
                  <div className="h-24 w-full bg-ink-800" />
                )}
                {!busy && (
                  <button
                    type="button"
                    onClick={() => setFiles(files.filter((x) => x !== f))}
                    className="absolute right-1 top-1 rounded-md bg-black/70 px-1.5 py-0.5 text-[10px] text-ink-200 opacity-0 transition group-hover:opacity-100"
                    aria-label={`Remove ${f.name}`}
                  >
                    remove
                  </button>
                )}
                {t && (
                  <div className="absolute inset-x-0 bottom-0 space-y-0.5 bg-gradient-to-t from-black/90 to-transparent px-1.5 pb-1 pt-3">
                    <div className="truncate text-[9px] font-medium text-ink-100">
                      {SUBJECT_LABELS[t.triage.subject] ?? t.triage.subject}
                    </div>
                    <div className="truncate text-[9px] text-ink-400">
                      {VIEW_LABELS[t.triage.view] ?? t.triage.view}
                      {bad ? " · unusable" : ""}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onSubmit}
          disabled={busy || files.length === 0}
          className="relative overflow-hidden rounded-xl bg-accent-500 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-accent-400 disabled:cursor-not-allowed disabled:bg-ink-700 disabled:text-ink-400"
        >
          {busy ? "Appraising…" : "Appraise this truck"}
        </button>
        <button
          type="button"
          onClick={() => setShowDetails((v) => !v)}
          className="rounded-xl border border-ink-600 px-3.5 py-2.5 text-sm text-ink-300 transition hover:border-ink-400 hover:text-ink-100"
        >
          {showDetails ? "Hide seller details" : "Add seller details (optional)"}
        </button>
        {(files.length > 0 || form.year || form.make) && !busy && (
          <button
            type="button"
            onClick={onReset}
            className="rounded-xl border border-ink-700 px-3.5 py-2.5 text-sm text-ink-400 transition hover:border-ink-500 hover:text-ink-200"
          >
            Clear
          </button>
        )}
      </div>

      {showDetails && (
        <div className="rounded-2xl border border-ink-700 bg-ink-900/60 p-4">
          <p className="mb-3 text-xs text-ink-400">
            Optional. Anything typed here is treated as an unverified claim: the photos are checked
            against it, and disagreements are reported rather than smoothed over.
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            {(
              [
                ["year", "Year", "2018"],
                ["make", "Make", "Mercedes-Benz"],
                ["model", "Model", "Actros 1845"],
                ["km", "Kilometres", "650000"],
                ["asking_price_try", "Asking price (TL)", "2450000"],
              ] as [keyof SellerForm, string, string][]
            ).map(([key, label, placeholder]) => (
              <label key={key} className="block">
                <span className="mb-1 block text-[10px] uppercase tracking-wider text-ink-400">
                  {label}
                </span>
                <input
                  value={form[key]}
                  onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                  placeholder={placeholder}
                  inputMode={key === "make" || key === "model" ? "text" : "numeric"}
                  className="w-full rounded-lg border border-ink-600 bg-ink-850 px-2.5 py-1.5 text-sm text-ink-100 placeholder:text-ink-500 focus:border-accent-400 focus:outline-none"
                />
              </label>
            ))}
          </div>
        </div>
      )}

      {samples.length > 0 && (
        <div className="rounded-2xl border border-ink-700 bg-ink-900/60 p-4">
          <div className="mb-2.5 text-[11px] uppercase tracking-wider text-ink-400">
            Or try a prepared case
          </div>
          <div className="flex flex-wrap gap-2">
            {samples.map((s) => (
              <button
                key={s.id}
                type="button"
                disabled={busy}
                onClick={() => onSample(s.id)}
                title={s.description}
                className={`rounded-xl border px-3 py-2 text-left text-xs transition disabled:opacity-50 ${
                  s.kind === "adversarial"
                    ? "border-red-500/30 bg-red-500/8 text-red-200 hover:border-red-400/50"
                    : "border-ink-600 bg-ink-850 text-ink-200 hover:border-accent-400/50"
                }`}
              >
                <div className="font-medium">{s.label}</div>
                <div className="mt-0.5 max-w-[16rem] truncate text-[10px] text-ink-400">
                  {s.description}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function StageProgress({
  stages,
  current,
  messages,
  busy,
}: {
  stages: { id: string; label: string }[];
  current: string | null;
  messages: Record<string, string>;
  busy: boolean;
}) {
  if (!busy && !current) return null;
  const activeIndex = stages.findIndex((s) => s.id === current);

  return (
    <div className="rise rounded-2xl border border-ink-700 bg-ink-900/70 p-4">
      <ol className="space-y-2">
        {stages.map((stage, i) => {
          const done = activeIndex > i || current === "done";
          const active = current === stage.id && current !== "done";
          if (stage.id === "done") return null;
          return (
            <li key={stage.id} className="flex items-center gap-3">
              <span
                className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px] ${
                  done
                    ? "border-accent-500 bg-accent-500/20 text-accent-300"
                    : active
                      ? "border-accent-400 bg-accent-500/10 text-accent-300"
                      : "border-ink-600 text-ink-500"
                }`}
              >
                {done ? "✓" : active ? <span className="pulse-dot">●</span> : i + 1}
              </span>
              <span
                className={`text-sm ${done ? "text-ink-400" : active ? "text-ink-100" : "text-ink-500"}`}
              >
                {messages[stage.id] || stage.label}
              </span>
              {active && (
                <span className="sweep relative ml-auto h-0.5 w-20 overflow-hidden rounded bg-ink-700" />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
