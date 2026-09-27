import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../../test/msw";
import { renderApp } from "../../../test/render";

function anonymous() {
  let session: object | null = null;
  server.use(
    http.get("/api/auth/session", () =>
      session ? HttpResponse.json(session) : HttpResponse.json({ code: "unauthorized", message: "" }, { status: 401 }),
    ),
    http.get("/api/me", () =>
      HttpResponse.json({ id: 1, display_name: "Ivan", login: "ivan", status: "active", expires_on: null, max_configs: 3, configs_count: 0 }),
    ),
    http.get("/api/me/servers", () => HttpResponse.json([])),
    http.get("/api/me/configs", () => HttpResponse.json([])),
    http.get("/api/admin/users", () => HttpResponse.json([])),
  );
  return {
    signIn: (role: "user" | "admin" = "user") => {
      session = { role, id: 1, login: role === "admin" ? "admin" : "ivan" };
    },
  };
}

describe("user login", () => {
  it("signs in and returns to the requested page", async () => {
    const s = anonymous();
    let body: unknown = null;
    server.use(
      http.post("/api/auth/login", async ({ request }) => {
        body = await request.json();
        s.signIn();
        return HttpResponse.json({ token: "t", role: "user" });
      }),
    );
    const { router } = renderApp("/login?next=%2Faccount");
    await userEvent.type(await screen.findByLabelText(/Логин/), "ivan");
    await userEvent.type(screen.getByLabelText(/Пароль/), "secret-password");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/account"));
    expect(body).toEqual({ login: "ivan", password: "secret-password" }); // the server decides the role
  });

  it("signs in again after a sign out left an empty session in the cache", async () => {
    const s = anonymous();
    server.use(
      http.post("/api/auth/login", () => {
        s.signIn();
        return HttpResponse.json({ token: "t", role: "user" });
      }),
    );
    const { router, queryClient } = renderApp("/login");
    queryClient.setQueryData(["session"], null); // what sign out leaves behind
    await userEvent.type(await screen.findByLabelText(/Логин/), "ivan");
    await userEvent.type(screen.getByLabelText(/Пароль/), "secret-password");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("heading", { name: "Мои конфиги" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/");
  });

  it("shows a translated error for wrong credentials", async () => {
    anonymous();
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json({ code: "invalid_credentials", message: "wrong" }, { status: 401 }),
      ),
    );
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText(/Логин/), "ivan");
    await userEvent.type(screen.getByLabelText(/Пароль/), "x");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Неверный логин или пароль");
  });

  it("links to invite registration", async () => {
    anonymous();
    const { router } = renderApp("/login");
    await userEvent.click(await screen.findByRole("link", { name: "Ввести ключ для регистрации" }));
    expect(router.state.location.pathname).toBe("/invite");
  });

  it("sends an administrator to the admin area from the same form", async () => {
    const s = anonymous();
    server.use(
      http.post("/api/auth/login", () => {
        s.signIn("admin");
        return HttpResponse.json({ token: "t", role: "admin" });
      }),
    );
    const { router } = renderApp("/login");
    await userEvent.type(await screen.findByLabelText(/Логин/), "admin");
    await userEvent.type(screen.getByLabelText(/Пароль/), "secret-password");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/users"));
  });

  it("keeps the old admin sign-in address working", async () => {
    anonymous();
    const { router } = renderApp("/admin/login?next=%2Fadmin%2Fservers");
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(new URLSearchParams(router.state.location.search).get("next")).toBe("/admin/servers");
  });
});

describe("invite registration", () => {
  it("checks the key as typed, then registers", async () => {
    const s = anonymous();
    const calls: unknown[] = [];
    server.use(
      http.post("/api/auth/invite/check", async ({ request }) => {
        calls.push(await request.json());
        return HttpResponse.json({ ok: true });
      }),
      http.post("/api/auth/invite/redeem", async ({ request }) => {
        calls.push(await request.json());
        s.signIn();
        return HttpResponse.json({ token: "t", role: "user" });
      }),
    );
    const { router } = renderApp("/invite");
    await userEvent.type(await screen.findByLabelText(/Ключ/), "abcd efgh");
    await userEvent.click(screen.getByRole("button", { name: "Далее" }));
    await userEvent.type(await screen.findByLabelText(/Логин/), "ivan");
    await userEvent.type(screen.getByLabelText(/^Пароль/), "Vq7#mZ2!rT9p@Lx");
    await userEvent.type(screen.getByLabelText(/Повторите пароль/), "Vq7#mZ2!rT9p@Lx");
    await userEvent.click(screen.getByRole("button", { name: "Зарегистрироваться" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(calls).toEqual([
      { key: "abcd efgh" },
      { key: "abcd efgh", login: "ivan", password: "Vq7#mZ2!rT9p@Lx" },
    ]);
  });

  it("rejects an invalid key", async () => {
    anonymous();
    server.use(
      http.post("/api/auth/invite/check", () =>
        HttpResponse.json({ code: "invite_invalid", message: "" }, { status: 404 }),
      ),
    );
    renderApp("/invite");
    await userEvent.type(await screen.findByLabelText(/Ключ/), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Далее" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ключ недействителен или уже использован");
  });

  it("shows server password errors and mismatched repeats", async () => {
    anonymous();
    server.use(
      http.post("/api/auth/invite/check", () => HttpResponse.json({ ok: true })),
      http.post("/api/auth/invite/redeem", () =>
        HttpResponse.json({ code: "password_too_weak", message: "" }, { status: 422 }),
      ),
    );
    renderApp("/invite");
    await userEvent.type(await screen.findByLabelText(/Ключ/), "KEY");
    await userEvent.click(screen.getByRole("button", { name: "Далее" }));
    await userEvent.type(await screen.findByLabelText(/Логин/), "ivan");
    await userEvent.type(screen.getByLabelText(/^Пароль/), "Vq7#mZ2!rT9p@Lx");
    await userEvent.type(screen.getByLabelText(/Повторите пароль/), "different-one");
    await userEvent.click(screen.getByRole("button", { name: "Зарегистрироваться" }));
    expect(await screen.findByText("Пароли не совпадают")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText(/Повторите пароль/));
    await userEvent.type(screen.getByLabelText(/Повторите пароль/), "Vq7#mZ2!rT9p@Lx");
    await userEvent.click(screen.getByRole("button", { name: "Зарегистрироваться" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Пароль слишком простой");
  });
});
