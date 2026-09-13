import type { AppraisalResult, PipelineInfo, ProgressEvent, SampleCase } from "./types";

export interface SellerDetails {
  year?: string;
  make?: string;
  model?: string;
  km?: string;
  asking_price_try?: string;
}

/** Downscale in the browser so uploads are fast on a phone connection. */
export async function downscale(file: File, maxEdge = 1400): Promise<File> {
  if (!file.type.startsWith("image/")) return file;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
    if (scale >= 1 && file.size < 1_800_000) {
      bitmap.close();
      return file;
    }
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return file;
    ctx.drawImage(bitmap, 0, 0, w, h);
    bitmap.close();
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.9),
    );
    if (!blob) return file;
    const name = file.name.replace(/\.(heic|heif|png|webp)$/i, ".jpg");
    return new File([blob], name, { type: "image/jpeg" });
  } catch {
    return file;
  }
}

function buildForm(files: File[], details: SellerDetails, noCache: boolean): FormData {
  const form = new FormData();
  files.forEach((f) => form.append("files", f, f.name));
  (Object.keys(details) as (keyof SellerDetails)[]).forEach((key) => {
    const value = details[key];
    if (value != null && String(value).trim() !== "") form.append(key, String(value).trim());
  });
  if (noCache) form.append("no_cache", "1");
  return form;
}

export async function fetchInfo(): Promise<PipelineInfo> {
  const res = await fetch("/api/info");
  if (!res.ok) throw new Error(`info failed: ${res.status}`);
  return res.json();
}

export async function fetchSamples(): Promise<SampleCase[]> {
  try {
    const res = await fetch("/api/samples");
    if (!res.ok) return [];
    const data = await res.json();
    return data.samples ?? [];
  } catch {
    return [];
  }
}

export async function appraiseSample(id: string): Promise<AppraisalResult> {
  const res = await fetch(`/api/appraise/sample/${encodeURIComponent(id)}`, { method: "POST" });
  if (!res.ok) throw new Error(`sample appraisal failed: ${res.status}`);
  return res.json();
}

/**
 * Stream an appraisal, reporting each stage as it happens.
 *
 * Uses fetch with a readable stream rather than EventSource, because the request
 * has to carry the uploaded photos.
 */
export async function appraiseStreaming(
  files: File[],
  details: SellerDetails,
  onProgress: (event: ProgressEvent) => void,
  noCache = false,
): Promise<AppraisalResult> {
  const res = await fetch("/api/appraise/stream", {
    method: "POST",
    body: buildForm(files, details, noCache),
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `appraisal failed: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: AppraisalResult | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      let payload: any;
      try {
        payload = JSON.parse(line.slice(6));
      } catch {
        continue;
      }
      if (payload.type === "progress") {
        onProgress({ stage: payload.stage, message: payload.message, data: payload.data ?? {} });
      } else if (payload.type === "result") {
        result = payload.result as AppraisalResult;
      } else if (payload.type === "error") {
        throw new Error(payload.message ?? "appraisal failed");
      }
    }
  }

  if (!result) throw new Error("The appraisal stream ended without a result.");
  return result;
}
