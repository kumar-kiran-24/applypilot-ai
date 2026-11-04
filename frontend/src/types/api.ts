/** Types mirroring the FastAPI schemas in `backend/app/schemas`. */

export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  profile_picture: string | null;
}

export interface GmailStatus {
  connected: boolean;
  is_active: boolean;
  gmail_email: string | null;
  connected_at: string | null;
  granted_scopes: string[];
}

export interface GmailConnectResponse {
  authorization_url: string;
  required_scopes: string[];
  message: string;
}

export interface GmailDisconnectResponse {
  disconnected: boolean;
  token_revoked: boolean;
  message: string;
}

export interface GlassdoorJob {
  job_title: string;
  company: string | null;
  location: string | null;
  job_url: string;
  email_subject: string;
  received_at: string | null;
  job_description: string | null;
  message_id: string;
}

export interface GlassdoorJobsResponse {
  messages_scanned: number;
  jobs_found: number;
  jobs: GlassdoorJob[];
}

/** Body of `GET /api/auth/session` (frontend BFF, not a backend route). */
export interface SessionResponse {
  authenticated: boolean;
  user: AuthUser | null;
}

export interface ValidationDetail {
  loc: Array<string | number>;
  msg: string;
}
