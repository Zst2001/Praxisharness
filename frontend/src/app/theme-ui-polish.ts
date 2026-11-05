import { theme, type ThemeConfig } from "antd";
import type { AppearanceMode } from "../api/settings";

export const systemFontStack =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif';

const light = {
  accent: "#3156c8", "accent-dark": "#2443a5", "on-accent": "#ffffff",
  bg: "#f7f9fc", surface: "#ffffff", "sidebar-bg": "#f1f4f9",
  border: "#e1e6ef", "border-strong": "#bdc8da", text: "#202c42", muted: "#5f6d83",
  disabled: "#8d98ab", selected: "#e9eefb", hover: "#edf1f8", "surface-subtle": "#f5f7fb",
  "user-bubble": "#edf1fa", focus: "#3156c8", "accent-soft": "#eaf0ff",
  "accent-border": "#bbcafa", "accent-glow": "rgba(49, 86, 200, 0.10)",
  success: "#16803d", "success-bg": "#edf8f0", warning: "#a96408",
  error: "#c52b32", "error-bg": "#fff1f0", "error-border": "#efb7b9",
  info: "#246899", "info-bg": "#edf5fa", "info-border": "#b5d3e6",
  "surface-overlay": "rgba(255, 255, 255, 0.94)",
  shadow: "rgba(0, 0, 0, 0.12)", "shadow-soft": "rgba(0, 0, 0, 0.06)",
  "shadow-strong": "rgba(0, 0, 0, 0.16)", "shimmer-highlight": "#ffffff",
};

export const palettes: Record<AppearanceMode, typeof light> = {
  light,
  dark: {
    accent: "#9db4ff", "accent-dark": "#bdccff", "on-accent": "#17254d",
    bg: "#141b29", surface: "#1b2435", "sidebar-bg": "#172030",
    border: "#303e54", "border-strong": "#50617c", text: "#edf1fa", muted: "#a9b6ca",
    disabled: "#728198", selected: "#2b3a57", hover: "#26334a", "surface-subtle": "#202c40",
    "user-bubble": "#283752", focus: "#9db4ff", "accent-soft": "#263859",
    "accent-border": "#526eae", "accent-glow": "rgba(157, 180, 255, 0.12)",
    success: "#87d9a2", "success-bg": "#233a2c", warning: "#ecc17c",
    error: "#ff9da1", "error-bg": "#40272a", "error-border": "#79464b",
    info: "#96c9ed", "info-bg": "#273640", "info-border": "#496879",
    "surface-overlay": "rgba(27, 36, 53, 0.96)",
    shadow: "rgba(0, 0, 0, 0.28)", "shadow-soft": "rgba(0, 0, 0, 0.18)",
    "shadow-strong": "rgba(0, 0, 0, 0.4)", "shimmer-highlight": "#e5e5e5",
  },
};

export function applyAppearance(mode: AppearanceMode) {
  const root = document.documentElement;
  root.dataset.appearance = mode;
  root.style.colorScheme = mode;
  for (const [name, value] of Object.entries(palettes[mode])) root.style.setProperty(`--${name}`, value);
}

export function appearanceTheme(mode: AppearanceMode): ThemeConfig {
  const p = palettes[mode];
  return {
    cssVar: { prefix: "praxis" },
    algorithm: mode === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: p.accent, colorInfo: p.info, colorSuccess: p.success,
      colorWarning: p.warning, colorError: p.error,
      colorText: p.text, colorTextSecondary: p.muted, colorTextDescription: p.muted,
      colorTextDisabled: p.disabled, colorTextLightSolid: p["on-accent"],
      colorBgBase: p.surface, colorBgLayout: p.bg, colorBgContainer: p.surface,
      colorBgElevated: p.surface, colorBorder: p.border, colorBorderSecondary: p.border,
      controlItemBgActive: p.selected, controlItemBgHover: p.hover,
      borderRadius: 10, controlHeight: 38, fontFamily: systemFontStack,
      fontSize: 16, wireframe: false,
    },
  };
}

export function terminalTheme(mode: AppearanceMode) {
  const p = palettes[mode];
  return {
    background: p.surface, foreground: p.text, cursor: p.accent, cursorAccent: p["on-accent"],
    selectionBackground: p.selected, selectionForeground: p.text,
    black: mode === "dark" ? "#898d8f" : "#242424", brightBlack: p.muted,
    red: p.error, brightRed: p.error, green: p.success, brightGreen: p.success,
    yellow: p.warning, brightYellow: p.warning, blue: p.info, brightBlue: p.info,
    magenta: mode === "dark" ? "#d9afea" : "#88439e",
    brightMagenta: mode === "dark" ? "#e6c7f2" : "#743b85",
    cyan: mode === "dark" ? "#8bd3db" : "#236e78",
    brightCyan: mode === "dark" ? "#ace3e8" : "#256571",
    white: mode === "dark" ? "#cccccc" : "#555555", brightWhite: p.text,
  };
}
