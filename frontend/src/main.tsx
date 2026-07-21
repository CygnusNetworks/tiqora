import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./i18n";
import "./themes/tokens.css";
import "./index.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("Root element #root not found");
}

async function bootstrap() {
  // Public demo build: start the MSW mock backend before mounting.
  if (import.meta.env.VITE_DEMO === "1") {
    const { startDemo } = await import("./demo/start");
    await startDemo(import.meta.env.BASE_URL);
  }
  createRoot(root!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void bootstrap();
