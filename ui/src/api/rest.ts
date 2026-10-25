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

/** Asks the person for a fresh authenticator code (#363). `confirm(code)` sends it to the
 * server (throws ApiError on a wrong code, so the asker can say so and ask again). Resolves
 * true once confirmed, false if the person cancels. */
export type StepUp = (reason: string, confirm: (code: string) => Promise<void>) => Promise<boolean>;

export class Rest {
  /** Set by the shell: privileged changes answered 428 "step-up: ..." ask for a code and are
   * sent once more (#363). Without it the 428 is an error like any other. */
  stepUp: StepUp | null = null;

  /** Ask `ask` for step-up codes from now on; the returned function stops it (only if no newer
   * asker took over meanwhile). */
  askStepUpWith(ask: StepUp): () => void {
    this.stepUp = ask;
    return () => {
      if (this.stepUp === ask) this.stepUp = null;
    };
  }

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

  private async call<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
    const init: RequestInit = { method, credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      init.headers = { "content-type": "application/json" };
      init.body = JSON.stringify(body);
    }
    let response: Response;
    try {
      response = await this.fetcher(path, init);
    } catch {
      throw new ApiError(0, "can't reach the Laddrix server");
    }
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    const data: unknown = text ? safeJson(text) : undefined;
    if (!response.ok) {
      const message = describe(data) ?? `${response.status} ${response.statusText}`;
      if (response.status === 428 && message.startsWith("step-up") && this.stepUp && !retried) {
        const confirm = (code: string) => this.call<void>("POST", "/api/me/step-up", { code }, true);
        if (await this.stepUp(message.replace(/^step-up: /, ""), confirm)) {
          return this.call<T>(method, path, body, true); // once: a second 428 is an error
        }
      }
      throw new ApiError(response.status, message);
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
