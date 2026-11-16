/**
 * Minimal local API for frontend Playwright runs.
 *
 * Only the server-side calls the BFF makes (auth/me, health) reach this
 * process. Browser data calls are fulfilled by Playwright fixtures. It never
 * contacts Google, Gmail, Groq or an employer.
 */
import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_API_PORT ?? 8010);

const user = {
  id: "99999999-9999-4999-8999-999999999999",
  email: "pilot.tester@example.com",
  full_name: "Ada Lovelace",
  profile_picture: null,
};

function json(response, status, body) {
  const payload = JSON.stringify(body);
  response.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(payload),
    "Cache-Control": "no-store",
  });
  response.end(payload);
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://127.0.0.1:${PORT}`);

  if (url.pathname === "/health") {
    json(response, 200, { status: "ok" });
    return;
  }

  if (url.pathname === "/api/v1/auth/me") {
    const authorization = request.headers.authorization ?? "";
    if (authorization === "Bearer expired-token") {
      json(response, 401, {
        detail: "Your session has expired. Sign in again.",
        code: "authentication_required",
      });
      return;
    }
    if (authorization !== "Bearer valid-token") {
      json(response, 401, {
        detail: "Authentication required.",
        code: "authentication_required",
      });
      return;
    }
    json(response, 200, user);
    return;
  }

  json(response, 404, { detail: "Not found.", code: "not_found" });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`mock backend listening on http://127.0.0.1:${PORT}`);
});

function shutdown() {
  server.close(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
