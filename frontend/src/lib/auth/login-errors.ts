/**
 * User-facing copy for the short error identifiers the backend appends to the
 * login callback redirect. Only known keys are ever rendered, so a crafted
 * `?error=` value cannot inject text into the page.
 */
const LOGIN_ERROR_MESSAGES: Record<string, string> = {
  state: "Your Google sign-in could not be verified. Start again from this page and allow cookies for this site.",
  oauth:
    "Google sign-in was cancelled or could not be completed. Try again to continue.",
  identity:
    "Google did not return a verified account. Sign in with a Google account that has a verified email address.",
  email_unverified:
    "Your Google account's email address is not verified, so it cannot be used to sign in.",
  account_exists:
    "An ApplyPilot account already uses this email address. Sign in with the Google account it was created with.",
  inactive: "This ApplyPilot account is inactive. Contact support if this is unexpected.",
  unavailable:
    "Sign-in is temporarily unavailable. Please try again in a moment.",
};

const GENERIC_MESSAGE =
  "Sign-in could not be completed. Start again from the login page.";

export function loginErrorMessage(reason: string | null | undefined): string {
  if (reason && Object.hasOwn(LOGIN_ERROR_MESSAGES, reason)) {
    return LOGIN_ERROR_MESSAGES[reason] ?? GENERIC_MESSAGE;
  }
  return GENERIC_MESSAGE;
}

export { GENERIC_MESSAGE as GENERIC_LOGIN_ERROR_MESSAGE };
