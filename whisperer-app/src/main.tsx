import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./theme.css";

document.addEventListener(
  "scroll",
  (e) => {
    const t = e.target as HTMLElement | null;
    if (!t || !t.classList || !t.classList.contains("scroll")) return;
    t.classList.add("is-scrolling");
    const w = t as HTMLElement & { __sbTimer?: number };
    if (w.__sbTimer) clearTimeout(w.__sbTimer);
    w.__sbTimer = window.setTimeout(() => t.classList.remove("is-scrolling"), 700);
  },
  true
);

ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
