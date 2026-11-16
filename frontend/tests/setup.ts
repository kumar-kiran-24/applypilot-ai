import { configure } from "@testing-library/react";

/**
 * Full-suite runs execute many jsdom files in parallel, so a query that waits
 * for React Query to resolve can take longer than Testing Library's 1s default
 * even though it always settles. Assertions are unchanged; only the wait is
 * longer.
 */
configure({ asyncUtilTimeout: 5_000 });
