// The entry point: loads the styles and every widget, then draws the app into the page's #root.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "dockview-react/dist/styles/dockview.css";
import "./styles/theme.css";
import "./styles/shell.css";
import "./styles/widgets.css";
import "./widgets";
import { App } from "./App";
import { AppProvider, createApp } from "./app/context";

const app = createApp();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppProvider app={app}>
      <App />
    </AppProvider>
  </StrictMode>,
);
