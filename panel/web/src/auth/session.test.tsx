import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { server } from "../../test/msw";
import { renderApp } from "../../test/render";

describe("route guards", () => {
  it("sends anonymous visitors of the cabinet to the user login", async () => {
    server.use(http.get("/api/auth/session", () => HttpResponse.json({ code: "unauthorized", message: "" }, { status: 401 })));
    renderApp("/account");
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeInTheDocument();
  });

  it("sends anonymous visitors of the admin area to the same login", async () => {
    server.use(http.get("/api/auth/session", () => HttpResponse.json({ code: "unauthorized", message: "" }, { status: 401 })));
    const { router } = renderApp("/admin/users");
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
  });

  it("does not let a user into the admin area", async () => {
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json({ role: "user", id: 1, login: "ivan" })),
      http.get("/api/me", () =>
        HttpResponse.json({ id: 1, display_name: "Ivan", login: "ivan", status: "active", expires_on: null, max_configs: 3, configs_count: 0 }),
      ),
      http.get("/api/me/servers", () => HttpResponse.json([])),
      http.get("/api/me/configs", () => HttpResponse.json([])),
    );
    const { router } = renderApp("/admin/users");
    expect(await screen.findByRole("heading", { name: "Мои конфиги" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/");
  });

  it("sends an administrator away from the user cabinet", async () => {
    server.use(
      http.get("/api/auth/session", () => HttpResponse.json({ role: "admin", id: 2, login: "admin" })),
      http.get("/api/admin/users", () => HttpResponse.json([])),
    );
    const { router } = renderApp("/account");
    await screen.findByRole("heading", { name: "Пользователи" });
    expect(router.state.location.pathname).toBe("/admin/users");
  });

  it("follows next only inside the signed-in role's own area and site", async () => {
    const { nextPath } = await import("./session");
    expect(nextPath("?next=%2Fadmin%2Fservers", "admin")).toBe("/admin/servers");
    expect(nextPath("?next=%2Faccount", "user")).toBe("/account");
    expect(nextPath("?next=%2Fadmin%2Fusers", "user")).toBe("/");
    expect(nextPath("?next=%2Faccount", "admin")).toBe("/admin");
    expect(nextPath("?next=%2F%2Fevil.example", "user")).toBe("/");
    expect(nextPath("?next=%2F%5Cevil.example", "user")).toBe("/");
    expect(nextPath("?next=https%3A%2F%2Fevil.example", "admin")).toBe("/admin");
  });

  it("translates API error codes", async () => {
    const { errorText } = await import("../i18n");
    const { ApiError } = await import("../api/client");
    const i18n = (await import("../i18n")).default;
    await i18n.changeLanguage("ru");
    expect(errorText(new ApiError(409, "config_limit", "x"))).toBe("Достигнут лимит конфигов");
    expect(errorText(new ApiError(418, "teapot", "I am a teapot"))).toBe("Что-то пошло не так (HTTP 418)");
    expect(errorText(new ApiError(502, "http_502", ""))).toBe("Что-то пошло не так (HTTP 502)");
    expect(errorText(new ApiError(409, "conflict", "x"))).toBe("Конфликт данных. Обновите страницу и повторите.");
  });
});
