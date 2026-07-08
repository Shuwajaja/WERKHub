import "@fontsource-variable/inter";
import "@fontsource/ibm-plex-mono";
import "./tokens.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

// Apply the persisted theme before first paint (no flash). Default = graphite (:root).
try {
  const saved = localStorage.getItem("werk-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
} catch {
  // storage unavailable — default theme applies
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
