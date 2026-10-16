// A QR code drawn as an SVG, for the authenticator link an admin hands to a new user (scan it
// with the phone's authenticator app). The squares are one SVG path; nothing is injected as HTML.
// Dark squares on white whatever the theme: phone cameras read that best.

import qrcode from "qrcode-generator";
import { useMemo } from "react";

/** The QR code's squares as an SVG path, `size` modules wide (with a 4-module quiet border). */
export function qrPath(text: string): { path: string; size: number } {
  const qr = qrcode(0, "M"); // the smallest version that fits, medium error correction
  qr.addData(text);
  qr.make();
  const n = qr.getModuleCount();
  let path = "";
  for (let r = 0; r < n; r += 1) {
    for (let c = 0; c < n; c += 1) {
      if (qr.isDark(r, c)) path += `M${c + 4} ${r + 4}h1v1h-1z`;
    }
  }
  return { path, size: n + 8 };
}

export function QrCode({ text, label, px = 176 }: { text: string; label: string; px?: number }) {
  const { path, size } = useMemo(() => qrPath(text), [text]);
  return (
    <svg className="qr" role="img" aria-label={label} width={px} height={px} viewBox={`0 0 ${size} ${size}`} shapeRendering="crispEdges">
      <rect width={size} height={size} fill="#fff" />
      <path d={path} fill="#000" />
    </svg>
  );
}
