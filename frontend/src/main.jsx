import React from "react";
import { createRoot } from "react-dom/client";
import { ApolloProvider } from "@apollo/client";
import { MotionConfig } from "motion/react";
import { ReactLenis } from "lenis/react";
import "lenis/dist/lenis.css";
import { client } from "./apollo.js";
import App from "./App.jsx";
import "./index.css";

// Lenis: scroll suave de toda la página. Se desactiva si el usuario pide menos movimiento.
const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ApolloProvider client={client}>
      {/* reducedMotion="user": Motion respeta prefers-reduced-motion en todas las animaciones */}
      <MotionConfig reducedMotion="user">
        {reducedMotion ? <App /> : (
          <ReactLenis root options={{ lerp: 0.1, wheelMultiplier: 0.9 }}>
            <App />
          </ReactLenis>
        )}
      </MotionConfig>
    </ApolloProvider>
  </React.StrictMode>
);
