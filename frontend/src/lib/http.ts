const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

interface ApiErrorBody {
  error: { status: number; code: string; message: string; details?: unknown[] };
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function readError(res: Response): Promise<{ message: string; code?: string }> {
  try {
    const body = (await res.json()) as Partial<ApiErrorBody>;
    if (body?.error?.message) return { message: body.error.message, code: body.error.code };
  } catch {
    // Proxies can return HTML. Fall back to the HTTP status instead.
  }
  return { message: res.statusText || `HTTP ${res.status}` };
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const { message, code } = await readError(res);
    throw new ApiError(res.status, message, code);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
