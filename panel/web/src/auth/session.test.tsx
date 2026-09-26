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

  it("sends anonymous visitors of the admin area to the admin login", async () => {
    server.use(http.get("/api/auth/session", () => HttpResponse.json({ code: "unauthorized", message: "" }, { status: 401 })));
    renderApp("/admin/users");
    expect(await screen.findByRole("heading", { name: "Вход администратора" })).toBeInTheDocument();
  });

  it("does not let a user into the admin area", async () => {
    server.use(http.get("/api/auth/session", () => HttpResponse.json({ role: "user", id: 1, login: "ivan" })));
    renderApp("/admin/users");
    expect(await screen.findByRole("heading", { name: "Вход администратора" })).toBeInTheDocument();
  });

  it("translates API error codes", async () => {
    const { errorText } = await import("../i18n");
    const { ApiError } = await import("../api/client");
    const i18n = (await import("../i18n")).default;
    await i18n.changeLanguage("ru");
    expect(errorText(new ApiError(409, "config_limit", "x"))).toBe("Достигнут лимит конфигов");
    expect(errorText(new ApiError(418, "teapot", "I am a teapot"))).toBe("I am a teapot");
  });
});
