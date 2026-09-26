import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "../../test/msw";
import { ApiError, api, setUnauthorizedHandler } from "./client";

describe("api client", () => {
  it("sends the CSRF cookie back as a header on unsafe methods only", async () => {
    document.cookie = "panel_csrf=tok123; path=/";
    const seen: Record<string, string | null> = {};
    server.use(
      http.get("/api/x", ({ request }) => {
        seen.get = request.headers.get("X-CSRF-Token");
        return HttpResponse.json({ ok: 1 });
      }),
      http.post("/api/x", async ({ request }) => {
        seen.post = request.headers.get("X-CSRF-Token");
        seen.body = JSON.stringify(await request.json());
        return HttpResponse.json({ ok: 2 });
      }),
    );
    expect(await api("/api/x")).toEqual({ ok: 1 });
    expect(await api("/api/x", { method: "POST", body: { a: 1 } })).toEqual({ ok: 2 });
    expect(seen).toEqual({ get: null, post: "tok123", body: '{"a":1}' });
  });

  it("turns error bodies into ApiError", async () => {
    server.use(http.post("/api/x", () => HttpResponse.json({ code: "config_limit", message: "limit" }, { status: 409 })));
    const err = await api("/api/x", { method: "POST" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "config_limit", message: "limit" });
  });

  it("handles non-JSON errors and 204", async () => {
    server.use(
      http.get("/api/boom", () => new HttpResponse("oops", { status: 502 })),
      http.delete("/api/x", () => new HttpResponse(null, { status: 204 })),
    );
    expect(await api("/api/boom").catch((e) => e)).toMatchObject({ status: 502, code: "http_502" });
    expect(await api("/api/x", { method: "DELETE" })).toBeNull();
  });

  it("reports 401 to the unauthorized handler", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    server.use(http.get("/api/me", () => HttpResponse.json({ code: "unauthorized", message: "" }, { status: 401 })));
    await api("/api/me").catch(() => {});
    expect(handler).toHaveBeenCalledOnce();
    setUnauthorizedHandler(null);
  });
});

describe("role mismatch", () => {
  it("treats 403 forbidden like a lost session", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    server.use(http.get("/api/admin/users", () => HttpResponse.json({ code: "forbidden", message: "" }, { status: 403 })));
    await api("/api/admin/users").catch(() => {});
    expect(handler).toHaveBeenCalledOnce();
    setUnauthorizedHandler(null);
  });
});
