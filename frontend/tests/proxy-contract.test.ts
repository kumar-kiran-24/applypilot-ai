/**
 * Contract test: every backend path the frontend clients call must be
 * permitted by the BFF route allowlist in `@/lib/constants`.
 *
 * The call list is **discovered from the client source** instead of being
 * hand-maintained. A hand-written list is exactly how Phase 2J.1 shipped
 * `jobs/import`, `.../destination` and `.../recover` in the clients while the
 * proxy rejected them: every component test mocks the API layer, so nothing
 * failed until a real browser hit the route handler.
 *
 * The scanner walks `src/features/ ∗/api.ts` with the TypeScript parser and
 * reads, for each `apiFetch(...)` call:
 *   - the first argument (string / template literal), with dynamic segments
 *     replaced by a UUID placeholder and query strings removed;
 *   - every string in the `method` option (`"POST"`, `"DELETE"`, and both
 *     branches of `saved ? "POST" : "DELETE"`), defaulting to GET.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import * as ts from "typescript";
import { describe, expect, it } from "vitest";

import { matchProxyRoute } from "@/lib/constants";

const SRC_DIR = fileURLToPath(new URL("../src", import.meta.url));
const FEATURES_DIR = join(SRC_DIR, "features");
const API_FETCH = "apiFetch";

/** A real UUID so the allow-list's UUID patterns match a concrete value. */
const DYNAMIC = "11111111-1111-4111-8111-111111111111";

export interface ClientCall {
  method: string;
  path: string;
  file: string;
}

/** Recursively collect the client API modules (`features/∗/api.ts`). */
function apiModules(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...apiModules(full));
    } else if (entry.name === "api.ts") {
      found.push(full);
    }
  }
  return found;
}

/**
 * Turn the first argument into a backend path.
 *
 * Only an identifier is treated as a path segment (an id). A call such as
 * `queryString(query)` or a conditional building `?…` is a query builder, so
 * it contributes nothing. Anything after `?` is dropped: the proxy matches on
 * the path and forwards a fixed set of query keys.
 */
function pathFrom(node: ts.Expression): string | null {
  let raw: string | null = null;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    raw = node.text;
  } else if (ts.isTemplateExpression(node)) {
    let built = node.head.text;
    for (const span of node.templateSpans) {
      built += ts.isIdentifier(span.expression) ? DYNAMIC : "";
      built += span.literal.text;
    }
    raw = built;
  }
  if (raw === null) {
    return null;
  }
  const [pathPart] = raw.split("?");
  return (pathPart ?? "").replace(/^\/+/, "");
}

/** Read the HTTP method(s) from the options object, defaulting to GET. */
function methodsFrom(options: ts.Expression | undefined): string[] {
  if (!options || !ts.isObjectLiteralExpression(options)) {
    return ["GET"];
  }
  const property = options.properties.find(
    (candidate): candidate is ts.PropertyAssignment =>
      ts.isPropertyAssignment(candidate) &&
      ((ts.isIdentifier(candidate.name) && candidate.name.text === "method") ||
        (ts.isStringLiteral(candidate.name) && candidate.name.text === "method")),
  );
  if (!property) {
    return ["GET"];
  }
  const methods: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isStringLiteral(node)) {
      methods.push(node.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(property.initializer);
  return methods.length > 0 ? [...new Set(methods)] : ["GET"];
}

/** Discover every `apiFetch` call in the client API modules. */
function discoverClientCalls(): ClientCall[] {
  const byKey = new Map<string, ClientCall>();
  for (const file of apiModules(FEATURES_DIR)) {
    const source = ts.createSourceFile(
      file,
      readFileSync(file, "utf8"),
      ts.ScriptTarget.Latest,
      /* setParentNodes */ true,
    );
    const relativeFile = relative(SRC_DIR, file).replaceAll("\\", "/");

    const visit = (node: ts.Node): void => {
      if (
        ts.isCallExpression(node) &&
        ts.isIdentifier(node.expression) &&
        node.expression.text === API_FETCH
      ) {
        const first = node.arguments[0];
        const path = first ? pathFrom(first) : null;
        if (path) {
          for (const method of methodsFrom(node.arguments[1])) {
            const key = `${method} ${path}`;
            if (!byKey.has(key)) {
              byKey.set(key, { method, path, file: relativeFile });
            }
          }
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  return [...byKey.values()].sort(
    (a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method),
  );
}

const CLIENT_CALLS = discoverClientCalls();

const A = "11111111-1111-4111-8111-111111111111";

/**
 * The Phase 2J.2 repair. Four of these are called by the clients (so the
 * scanner must find them); `form-analysis/fields` is a backend route the UI
 * does not call yet, allow-listed so the paired read is available without a
 * second proxy change.
 */
const REPAIRED_ROUTES: ReadonlyArray<readonly [string, string]> = [
  ["POST", "jobs/import"],
  ["GET", "browser-sessions/active"],
  ["POST", `browser-sessions/by-application/${A}/destination`],
  ["POST", `browser-sessions/by-application/${A}/recover`],
  ["GET", `browser-sessions/by-application/${A}/form-analysis/fields`],
];

/** Repaired routes that the client source actually calls. */
const CLIENT_REPAIRED_ROUTES = REPAIRED_ROUTES.filter(
  ([, path]) => !path.endsWith("/form-analysis/fields"),
);

describe("BFF proxy allowlist", () => {
  it("discovers the client call sites instead of trusting a hand-written list", () => {
    // A broken scanner would make the coverage assertion below pass vacuously.
    expect(CLIENT_CALLS.length).toBeGreaterThanOrEqual(60);
    for (const [method, path] of CLIENT_REPAIRED_ROUTES) {
      const normalised = path.replace(A, DYNAMIC);
      expect(
        CLIENT_CALLS.some(
          (call) => call.method === method && call.path === normalised,
        ),
        `scanner did not find ${method} ${normalised}`,
      ).toBe(true);
    }
    // No unresolved template expression leaked into a path.
    expect(CLIENT_CALLS.filter((call) => call.path.includes("${"))).toEqual([]);
  });

  it("permits every backend path the client actually calls", () => {
    const rejected = CLIENT_CALLS.filter(
      (call) => matchProxyRoute(call.method, call.path) === null,
    );
    const message = rejected
      .map(
        (call) =>
          `${call.method} ${call.path} (from ${call.file}) is not in PROXY_ROUTES`,
      )
      .join("\n");
    expect(rejected, message).toEqual([]);
  });

  it.each(REPAIRED_ROUTES)(
    "permits the repaired route %s %s",
    (method, path) => {
      expect(matchProxyRoute(method, path)).not.toBeNull();
    },
  );

  it("keeps the repaired routes method-specific", () => {
    expect(matchProxyRoute("GET", "jobs/import")).toBeNull();
    expect(matchProxyRoute("POST", "browser-sessions/active")).toBeNull();
    expect(
      matchProxyRoute(
        "POST",
        `browser-sessions/by-application/${A}/form-analysis/fields`,
      ),
    ).toBeNull();
    expect(
      matchProxyRoute("GET", `browser-sessions/by-application/${A}/destination`),
    ).toBeNull();
    expect(
      matchProxyRoute("GET", `browser-sessions/by-application/${A}/recover`),
    ).toBeNull();
  });

  it("keeps dynamic segments limited to UUIDs", () => {
    expect(
      matchProxyRoute("POST", "browser-sessions/by-application/not-a-uuid/recover"),
    ).toBeNull();
    expect(matchProxyRoute("POST", "browser-sessions/by-application/123/destination")).toBeNull();
    expect(matchProxyRoute("GET", "browser-sessions/by-application/abc/form-analysis/fields")).toBeNull();
  });

  it("forwards the saved-answer search filters", () => {
    const route = matchProxyRoute("GET", "profile/answers");
    expect(route).not.toBeNull();
    for (const key of ["q", "category", "trust"]) {
      expect(route?.queryKeys).toContain(key);
    }
  });

  it("forwards every job listing filter the client sends", () => {
    const route = matchProxyRoute("GET", "jobs");
    expect(route).not.toBeNull();
    for (const key of [
      "limit",
      "offset",
      "q",
      "company",
      "location",
      "source",
      "has_matches",
      "state",
      "sort",
    ]) {
      expect(route?.queryKeys).toContain(key);
    }
    // Paging on the saved list is forwarded too.
    expect(matchProxyRoute("GET", "jobs/saved")).not.toBeNull();
  });

  it("still rejects paths the frontend never calls", () => {
    expect(matchProxyRoute("GET", "metrics")).toBeNull();
    expect(matchProxyRoute("GET", "gmail/messages")).toBeNull();
    expect(matchProxyRoute("DELETE", "applications")).toBeNull();
    expect(matchProxyRoute("POST", "jobs")).toBeNull();
    // A near-miss of a repaired route must not be caught by a broad pattern.
    expect(matchProxyRoute("POST", "jobs/import/extra")).toBeNull();
    expect(
      matchProxyRoute("POST", `browser-sessions/by-application/${A}/recover-now`),
    ).toBeNull();
  });

  it("covers every route the application actually uses", () => {
    // Spot-check a few calls that must be discovered, including the ones whose
    // paths are built from template literals.
    const expected: ReadonlyArray<readonly [string, string]> = [
      ["GET", "applications"],
      ["POST", "applications/analyze"],
      ["PATCH", `applications/${DYNAMIC}/questions/${DYNAMIC}`],
      ["POST", `browser-sessions/by-application/${DYNAMIC}/fill`],
      ["GET", `browser-sessions/by-application/${DYNAMIC}/fill`],
      ["DELETE", `jobs/${DYNAMIC}/save`],
      ["POST", "matching/bulk"],
      ["GET", "resumes"],
      ["POST", "resumes/upload"],
      ["GET", "jobs/saved"],
    ];
    for (const [method, path] of expected) {
      expect(
        CLIENT_CALLS.some((call) => call.method === method && call.path === path),
        `missing discovered call ${method} ${path}`,
      ).toBe(true);
    }
  });
});
