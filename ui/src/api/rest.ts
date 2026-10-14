// Talking to the server's REST API. The session cookie travels automatically; changing
// requests are JSON, which (with the server's Origin check) blocks cross-site forgery.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
  /** 401: the session is gone (logged out, expired): show the login screen. */
  get loggedOut(): boolean {
    return this.status === 401;
  }
}

export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export class Rest {
  constructor(private readonly fetcher: Fetch = (i, init) => fetch(i, init)) {}

  get<T>(path: string): Promise<T> {
    return this.call<T>("GET", path);
  }
  post<T>(path: string, body?: unknown): Promise<T> {
    return this.call<T>("POST", path, body ?? {});
  }
  put<T>(path: string, body: unknown): Promise<T> {
    return this.call<T>("PUT", path, body);
  }
  patch<T>(path: string, body: unknown): Promise<T> {
    return this.call<T>("PATCH", path, body);
  }
  delete<T>(path: string): Promise<T> {
    return this.call<T>("DELETE", path);
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const init: RequestInit = { method, credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      init.headers = { "content-type": "application/json" };
      init.body = JSON.stringify(body);
    }
    let response: Response;
    try {
      response = await this.fetcher(path, init);
    } catch {
      throw new ApiError(0, "can't reach the MockTrade server");
    }
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    const data: unknown = text ? safeJson(text) : undefined;
    if (!response.ok) {
      throw new ApiError(response.status, describe(data) ?? `${response.status} ${response.statusText}`);
    }
    return data as T;
  }
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** The server's reason, as a sentence ({"detail": "..."} or a validation list). */
function describe(data: unknown): string | null {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0] as { msg?: string; loc?: unknown[] };
      const field = first.loc?.slice(-1)[0];
      return field ? `${String(field)}: ${first.msg}` : (first.msg ?? null);
    }
  }
  return typeof data === "string" && data ? data : null;
}
