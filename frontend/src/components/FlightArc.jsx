import { motion, useReducedMotion } from "motion/react";

// Arco de vuelo decorativo del hero: la ruta se dibuja y un avión la recorre.
// SVG + Motion (sin imágenes externas: la CSP solo admite img-src 'self').
const ARC = "M 40 380 C 220 120, 520 40, 760 120";
const ARC_2 = "M 120 430 C 330 300, 560 300, 780 330";

export default function FlightArc({ className }) {
  const reduce = useReducedMotion();
  return (
    <svg viewBox="0 0 800 460" fill="none" aria-hidden="true" className={className}>
      <defs>
        <linearGradient id="arc-gradient" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0" stopColor="oklch(0.78 0.1 225)" stopOpacity="0" />
          <stop offset="0.45" stopColor="oklch(0.78 0.1 225)" stopOpacity="0.8" />
          <stop offset="1" stopColor="oklch(0.68 0.19 38)" />
        </linearGradient>
      </defs>

      <path d={ARC_2} stroke="white" strokeOpacity="0.08" strokeWidth="1.5" strokeDasharray="2 10" strokeLinecap="round" />
      <motion.path d={ARC} stroke="url(#arc-gradient)" strokeWidth="2" strokeLinecap="round" strokeDasharray="1 9"
        initial={{ pathLength: reduce ? 1 : 0 }} animate={{ pathLength: 1 }} transition={{ duration: 2.2, ease: "easeInOut" }} />

      {[[40, 380, "BOG"], [760, 120, "CTG"]].map(([x, y, code], i) => (
        <motion.g key={code} initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: i ? 2.0 : 0.2, type: "spring", bounce: 0.4 }} style={{ originX: `${x}px`, originY: `${y}px` }}>
          <circle cx={x} cy={y} r="14" fill="oklch(0.68 0.19 38)" fillOpacity="0.18" />
          <circle cx={x} cy={y} r="5" fill={i ? "oklch(0.68 0.19 38)" : "oklch(0.78 0.1 225)"} />
          <text x={x} y={y + (i ? -26 : 36)} textAnchor="middle" fill="white" fillOpacity="0.75"
            fontFamily="Geist Mono, monospace" fontSize="15" letterSpacing="2">{code}</text>
        </motion.g>
      ))}

      {!reduce && (
        <motion.g
          initial={{ offsetDistance: "0%", opacity: 0 }}
          animate={{ offsetDistance: ["0%", "100%"], opacity: [0, 1, 1, 0] }}
          transition={{ duration: 6, delay: 2.2, repeat: Infinity, repeatDelay: 1.5, ease: "easeInOut", times: [0, 0.1, 0.9, 1] }}
          style={{ offsetPath: `path("${ARC}")`, offsetRotate: "auto" }}
        >
          {/* Avión (mismo trazo que el icono Plane de Lucide, orientado hacia la derecha) */}
          <g transform="translate(-12 -12) rotate(45 12 12)">
            <path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"
              fill="white" stroke="white" strokeWidth="1" strokeLinejoin="round" />
          </g>
        </motion.g>
      )}
    </svg>
  );
}
