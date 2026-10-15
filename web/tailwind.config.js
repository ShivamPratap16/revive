/** Revive design system. Dark-first: deep ink surfaces, one saffron accent, and a small
 *  set of signal colours, each tied to a part of the pipeline. */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#07090c", 900: "#0a0d12", 850: "#0d1117", 800: "#11161d",
          750: "#161c25", 700: "#1f2733", 600: "#2b3442",
        },
        fg: { DEFAULT: "#e8eaed", muted: "#9aa3ae", faint: "#6b7480" },
        saffron: { 300: "#ffd58a", 400: "#ffc457", 500: "#ffb224", 600: "#e69500" },
        // one colour per pipeline stage, reused in the diagram, the event log and charts
        sig: {
          event: "#58a6ff", detect: "#ffb224", nudge: "#2dd4bf", call: "#3fb950",
          webhook: "#8b9bb4", ai: "#a78bfa", action: "#ffb224", bad: "#f85149",
        },
      },
      fontFamily: {
        sans: ['"Geist Variable"', "system-ui", "sans-serif"],
        mono: ['"Geist Mono Variable"', "ui-monospace", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(255,178,36,.25), 0 8px 40px -8px rgba(255,178,36,.25)",
        card: "0 1px 0 0 rgba(255,255,255,.04) inset, 0 20px 40px -24px rgba(0,0,0,.6)",
      },
      keyframes: {
        pulseDot: { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.35 } },
        fadeIn: { from: { opacity: 0, transform: "translateY(3px)" }, to: { opacity: 1, transform: "none" } },
      },
      animation: { pulseDot: "pulseDot 1.6s ease-in-out infinite" },
    },
  },
  plugins: [],
};
