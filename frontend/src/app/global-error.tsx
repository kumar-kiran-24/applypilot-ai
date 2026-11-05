"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: "#070d1a",
          color: "#E8EEFA",
          fontFamily: "system-ui, sans-serif",
          padding: "24px",
        }}
      >
        <div style={{ maxWidth: 420, textAlign: "center" }}>
          <h1 style={{ fontSize: 18, margin: 0 }}>
            ApplyPilot could not start
          </h1>
          <p style={{ color: "#9DAAC2", fontSize: 13, lineHeight: 1.6 }}>
            An unexpected error stopped the application shell from rendering.
            {error.digest ? ` Reference: ${error.digest}` : ""}
          </p>
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: 16,
              padding: "10px 18px",
              borderRadius: 12,
              border: "1px solid rgba(255,255,255,0.16)",
              background: "linear-gradient(120deg,#3B82F6,#6366F1 50%,#8B5CF6)",
              color: "#fff",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
