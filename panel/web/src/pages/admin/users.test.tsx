import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { AdminUser, AdminUserDetail, ServerInfo } from "../../api/types";
import { EXPORT, adminSession, config, err } from "../../../test/fixtures";
import { server } from "../../../test/msw";
import { renderApp } from "../../../test/render";

function user(over: Partial<AdminUser> = {}): AdminUser {
  return {
    id: 1, display_name: "Anna", note: "", login: "anna", registered: true, status: "active", blocked_by: null,
    expires_on: "2026-12-31", max_configs: 3, configs_count: 1, traffic_total: { rx: 3 * 1024 ** 2, tx: 1024 },
    created_at: "2026-09-01T00:00:00Z", ...over,
  };
}

function detail(over: Partial<AdminUserDetail> = {}): AdminUserDetail {
  return {
    ...user(),
    configs: [config()],
    traffic_by_server: [{ server_id: 10, server_name: "nl-1", rx: 3 * 1024 ** 2, tx: 1024 }],
    ...over,
  };
}

const SERVER_INFO: ServerInfo = {
  id: 10, name: "nl-1", host: "203.0.113.10", ssh_port: 22, ssh_user: "root", enabled_for_users: true,
  host_key: "ssh-ed25519 AAAA", imported_at: "2026-09-26T00:00:00Z", last_ok_at: "2026-09-26T00:00:00Z",
  last_error: null, created_at: "2026-09-26T00:00:00Z", configs_count: 2,
  containers: [{ container: "amnezia-awg2", title: "AmneziaWG", port: "55424" }],
  load: "low", load_pct: 20, bandwidth_mbps: null, expected_clients: null, metrics_iface: null,
};

function userPage(d: AdminUserDetail) {
  adminSession();
  server.use(
    http.get("/api/admin/users/1", () => HttpResponse.json(d)),
    http.get("/api/admin/traffic", () =>
      HttpResponse.json({ rows: [{ day: "2026-09-26", server_id: 10, rx: 100, tx: 10 }], total: { rx: 100, tx: 10 } })),
    http.get("/api/admin/servers", () => HttpResponse.json([SERVER_INFO])),
  );
}

describe("admin users list", () => {
  it("lists users and searches", async () => {
    adminSession();
    const urls: string[] = [];
    server.use(
      http.get("/api/admin/users", ({ request }) => {
        urls.push(new URL(request.url).search);
        return HttpResponse.json([user(), user({ id: 2, display_name: "Boris", status: "blocked", blocked_by: "admin" })]);
      }),
    );
    renderApp("/admin/users");
    const row = (await screen.findByText("Anna")).closest("tr")!;
    expect(within(row).getByText("Активен")).toBeInTheDocument();
    expect(within(row).getByText("1 / 3")).toBeInTheDocument();
    expect(screen.getByText("Заблокирован администратором")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("Поиск"), "bor");
    await waitFor(() => expect(urls.at(-1)).toContain("q=bor"));
  });

  it("creates a user and shows the invite key once", async () => {
    adminSession();
    let body: unknown = null;
    server.use(
      http.get("/api/admin/users", () => HttpResponse.json([])),
      http.post("/api/admin/users", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ user: user({ registered: false, login: null }), invite_key: "ABCD-EFGH-IJKL" }, { status: 201 });
      }),
    );
    renderApp("/admin/users");
    await userEvent.click(await screen.findByRole("button", { name: "Новый пользователь" }));
    await userEvent.type(await screen.findByLabelText(/Имя/), "Anna");
    await userEvent.clear(screen.getByLabelText(/Лимит конфигов/));
    await userEvent.type(screen.getByLabelText(/Лимит конфигов/), "5");
    await userEvent.type(screen.getByLabelText(/Последний день доступа/), "2026-12-31");
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    expect(await screen.findByDisplayValue("ABCD-EFGH-IJKL")).toBeInTheDocument();
    expect(screen.getByText(/показывается один раз/)).toBeInTheDocument();
    expect(body).toEqual({ display_name: "Anna", max_configs: 5, expires_on: "2026-12-31", note: "" });
    await userEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    await waitFor(() => expect(screen.queryByDisplayValue("ABCD-EFGH-IJKL")).toBeNull());
  });
});

describe("admin user page", () => {
  it("shows profile, traffic by server and configs", async () => {
    userPage(detail());
    renderApp("/admin/users/1");
    expect(await screen.findByRole("heading", { name: "Anna" })).toBeInTheDocument();
    expect(screen.getByText(/Зарегистрирован/)).toBeInTheDocument();
    const byServer = screen.getByText("По серверам").parentElement!;
    expect(within(byServer).getByText(/nl-1/)).toBeInTheDocument();
    expect(screen.getByText("Phone")).toBeInTheDocument();
  });

  it("edits limit and expiry", async () => {
    userPage(detail());
    let body: unknown = null;
    server.use(
      http.patch("/api/admin/users/1", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(user());
      }),
    );
    renderApp("/admin/users/1");
    const limit = await screen.findByLabelText(/Лимит конфигов/);
    await userEvent.clear(limit);
    await userEvent.type(limit, "7");
    await userEvent.clear(screen.getByLabelText(/Последний день доступа/));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(body).toEqual({ display_name: "Anna", note: "", max_configs: 7, expires_on: null }));
  });

  it("does not save a cleared limit as zero", async () => {
    userPage(detail());
    let body: unknown = null;
    server.use(
      http.patch("/api/admin/users/1", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(user());
      }),
    );
    renderApp("/admin/users/1");
    await userEvent.clear(await screen.findByLabelText(/Лимит конфигов/));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await new Promise((r) => setTimeout(r, 100));
    expect(body).toBeNull();
  });

  it("blocks, unblocks and deletes the user", async () => {
    const calls: string[] = [];
    userPage(detail());
    server.use(
      http.post("/api/admin/users/1/block", () => {
        calls.push("block");
        return HttpResponse.json(user({ status: "blocked", blocked_by: "admin" }));
      }),
      http.delete("/api/admin/users/1", () => {
        calls.push("delete");
        return HttpResponse.json({ deleting: true }, { status: 202 });
      }),
      http.get("/api/admin/users", () => HttpResponse.json([])),
    );
    const { router } = renderApp("/admin/users/1");
    // The first buttons belong to the user header; the configs table has its own.
    await userEvent.click((await screen.findAllByRole("button", { name: "Заблокировать" }))[0]);
    await userEvent.click(await screen.findByRole("button", { name: "Да" }));
    await waitFor(() => expect(calls).toEqual(["block"]));
    await userEvent.click(screen.getAllByRole("button", { name: "Удалить" })[0]);
    await userEvent.click(await screen.findByRole("button", { name: "Да" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/users"));
    expect(calls).toEqual(["block", "delete"]);
  });

  it("offers a new invite key only before registration", async () => {
    userPage(detail({ registered: false, login: null }));
    server.use(http.post("/api/admin/users/1/invite", () => HttpResponse.json({ invite_key: "NEW-KEY" })));
    renderApp("/admin/users/1");
    await userEvent.click(await screen.findByRole("button", { name: "Выпустить новый ключ" }));
    await userEvent.click(await screen.findByRole("button", { name: "Да" }));
    expect(await screen.findByDisplayValue("NEW-KEY")).toBeInTheDocument();
  });

  it("hides the invite button for registered users", async () => {
    userPage(detail());
    renderApp("/admin/users/1");
    await screen.findByRole("heading", { name: "Anna" });
    expect(screen.queryByRole("button", { name: "Выпустить новый ключ" })).toBeNull();
  });

  it("issues a config for the user", async () => {
    userPage(detail());
    let body: unknown = null;
    server.use(
      http.post("/api/admin/users/1/configs", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(config({ id: 5 }), { status: 201 });
      }),
      http.get("/api/admin/configs/5", () => HttpResponse.json({ ...config({ id: 5 }), export: EXPORT })),
    );
    renderApp("/admin/users/1");
    await userEvent.click(await screen.findByRole("button", { name: "Выдать конфиг" }));
    await userEvent.click(await screen.findByRole("button", { name: /nl-1 · AmneziaWG/ }));
    expect(await screen.findByDisplayValue("vpn://KEY")).toBeInTheDocument();
    expect(body).toEqual({ server_id: 10, container: "amnezia-awg2" });
  });

  it("issues a config under the name the admin typed", async () => {
    let body: unknown = null;
    userPage(detail());
    server.use(
      http.post("/api/admin/users/1/configs", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(config({ id: 5 }), { status: 201 });
      }),
      http.get("/api/admin/configs/5", () => HttpResponse.json({ ...config({ id: 5 }), export: EXPORT })),
    );
    renderApp("/admin/users/1");
    await userEvent.click(await screen.findByRole("button", { name: "Выдать конфиг" }));
    await userEvent.type(await screen.findByLabelText("Название конфига"), "  Ноутбук ");
    await userEvent.click(screen.getByRole("button", { name: /nl-1 · AmneziaWG/ }));
    await screen.findByDisplayValue("vpn://KEY");
    expect(body).toEqual({ server_id: 10, container: "amnezia-awg2", name: "Ноутбук" });
  });

  it("shows the limit error when issuing fails", async () => {
    userPage(detail());
    server.use(http.post("/api/admin/users/1/configs", () => err(409, "config_limit")));
    renderApp("/admin/users/1");
    await userEvent.click(await screen.findByRole("button", { name: "Выдать конфиг" }));
    await userEvent.click(await screen.findByRole("button", { name: /nl-1 · AmneziaWG/ }));
    expect(await screen.findByText("Достигнут лимит конфигов")).toBeInTheDocument();
  });

  it("keeps unsaved edits when the page data refreshes", async () => {
    let current = detail();
    userPage(current);
    server.use(
      http.get("/api/admin/users/1", () => HttpResponse.json(current)),
      http.post("/api/admin/configs/1/block", () => {
        // the refetched user differs (the config is now blocked), so the query data gets a new reference
        current = detail({ configs: [config({ status: "blocked", blocked_by: "admin" })] });
        return HttpResponse.json(current.configs[0]);
      }),
    );
    renderApp("/admin/users/1");
    const limit = await screen.findByLabelText(/Лимит конфигов/);
    await userEvent.clear(limit);
    await userEvent.type(limit, "9");
    const row = screen.getByText("Phone").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Заблокировать" }));
    await userEvent.click(await screen.findByRole("button", { name: /^Да$/ }));
    expect(await within(row).findByText("Заблокирован администратором")).toBeInTheDocument();
    expect(screen.getByLabelText(/Лимит конфигов/)).toHaveValue("9");
  });

  it("admin can unblock any config", async () => {
    userPage(detail({ configs: [config({ status: "blocked", blocked_by: "user" })] }));
    let called = false;
    server.use(
      http.post("/api/admin/configs/1/unblock", () => {
        called = true;
        return HttpResponse.json(config());
      }),
    );
    renderApp("/admin/users/1");
    const row = (await screen.findByText("Phone")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Разблокировать" }));
    await waitFor(() => expect(called).toBe(true));
  });
});
