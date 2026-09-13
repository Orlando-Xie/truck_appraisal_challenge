import { useState } from "react";
import type { BBox } from "../types";

/**
 * Shows the exact region of the uploaded photo a finding refers to.
 *
 * A condition report that says "rust on the door" is an assertion. One that
 * shows the pixels is checkable, which is the difference between a buyer
 * trusting the report and ignoring it.
 */
export function EvidenceCrop({
  box,
  src,
  size = 76,
  pad = 0.16,
}: {
  box: BBox;
  src: string | undefined;
  size?: number;
  pad?: number;
}) {
  const [open, setOpen] = useState(false);
  if (!src) return null;

  // Clamp, then pad outward so the defect has visible context around it.
  const x0 = Math.max(0, Math.min(1, Math.min(box.x0, box.x1)));
  const x1 = Math.max(0, Math.min(1, Math.max(box.x0, box.x1)));
  const y0 = Math.max(0, Math.min(1, Math.min(box.y0, box.y1)));
  const y1 = Math.max(0, Math.min(1, Math.max(box.y0, box.y1)));

  let w = Math.max(0.04, x1 - x0);
  let h = Math.max(0.04, y1 - y0);
  const cx = x0 + w / 2;
  const cy = y0 + h / 2;
  w = Math.min(1, w * (1 + pad * 2));
  h = Math.min(1, h * (1 + pad * 2));

  // Square the crop so the thumbnails line up.
  const side = Math.min(1, Math.max(w, h));
  const left = Math.max(0, Math.min(1 - side, cx - side / 2));
  const top = Math.max(0, Math.min(1 - side, cy - side / 2));

  const zoom = 1 / side;
  const style: React.CSSProperties = {
    width: size,
    height: size,
    backgroundImage: `url(${src})`,
    backgroundSize: `${zoom * 100}% ${zoom * 100}%`,
    backgroundPosition: `${(left / Math.max(1e-6, 1 - side)) * 100}% ${(top / Math.max(1e-6, 1 - side)) * 100}%`,
    backgroundRepeat: "no-repeat",
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={`${box.filename} — click to see it in the full photo`}
        className="group relative shrink-0 overflow-hidden rounded-lg border border-ink-600 transition hover:border-accent-400"
        style={style}
      >
        <span className="absolute inset-x-0 bottom-0 bg-black/65 px-1 py-0.5 text-[9px] text-ink-200 opacity-0 transition group-hover:opacity-100">
          zoom
        </span>
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-6"
          onClick={() => setOpen(false)}
          role="dialog"
          aria-modal="true"
        >
          <div className="relative max-h-full max-w-4xl" onClick={(e) => e.stopPropagation()}>
            <div className="relative inline-block">
              <img src={src} alt={box.filename} className="max-h-[80vh] rounded-xl" />
              {/* The box is drawn over the full photo so the location is unambiguous. */}
              <div
                className="pointer-events-none absolute border-2 border-accent-400 shadow-[0_0_0_9999px_rgba(0,0,0,0.45)]"
                style={{
                  left: `${x0 * 100}%`,
                  top: `${y0 * 100}%`,
                  width: `${(x1 - x0) * 100}%`,
                  height: `${(y1 - y0) * 100}%`,
                }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-ink-300">
              <span>{box.filename}</span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded border border-ink-600 px-2 py-1 hover:border-ink-400"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
