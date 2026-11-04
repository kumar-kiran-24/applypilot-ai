import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        navy: {
          950: "#05080F",
          900: "#070D1A",
          850: "#0A1324",
          800: "#0E1A2E",
          750: "#122139",
          700: "#172A46",
          600: "#1F3557",
        },
        ink: {
          DEFAULT: "#E8EEFA",
          muted: "#9DAAC2",
          subtle: "#6C7B94",
        },
        brand: {
          blue: "#3B82F6",
          indigo: "#6366F1",
          violet: "#8B5CF6",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
      },
      borderRadius: {
        "2xl": "1rem",
        "3xl": "1.25rem",
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(99,102,241,0.25), 0 18px 50px -20px rgba(59,130,246,0.55)",
        card: "0 24px 60px -35px rgba(2, 6, 23, 0.9)",
      },
      backgroundImage: {
        "brand-gradient":
          "linear-gradient(120deg, #3B82F6 0%, #6366F1 50%, #8B5CF6 100%)",
        "surface-sheen":
          "radial-gradient(120% 120% at 0% 0%, rgba(59,130,246,0.14) 0%, rgba(7,13,26,0) 55%)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.35s ease-out both",
      },
    },
  },
  plugins: [],
};

export default config;
