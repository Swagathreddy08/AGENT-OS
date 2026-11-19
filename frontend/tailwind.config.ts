import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0b1020",
        panel: "#111827",
        violet: "#7c5cff",
      },
    },
  },
  plugins: [],
} satisfies Config;
