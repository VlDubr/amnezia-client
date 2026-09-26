import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { ServerInfo } from "../../api/types";
import { adminSession, config, err } from "../../../test/fixtures";
import { server } from "../../../test/msw";
import { renderApp } from "../../../test/render";

function srv(over: Partial<ServerInfo> = {}): ServerInfo {
  return {
    id: 10, name: "nl-1", host: "203.0.113.10", ssh_port: 22, ssh_user: "root", enabled_for_users: true,
    host_key: "ssh-ed25519 AAAA", imported_at: "2026-09-26T00:00:00Z", last_ok_at: "2026-09-26T00:00:00Z",
    last_error: null, created_at: "2026-09-26T00:00:00Z", configs_count: 2,
    containers: [{ container: "amnezia-awg2", title: "AmneziaWG", port: "55424" }], ...over,
  };
}

function jobs(sequence: string[]) {
  let i = 0;
  server.use(
    http.get("/api/jobs/:id", ({ params }) =>
      HttpResponse.json({ id: Number(params.id), kind: "x", status: sequence[Math.min(i++, sequence.length - 1)],
                          attempts: 0, last_error: null })),
  );
}

describe("servers", () => {
  it("lists servers with their protocols", async () => {
    adminSession();
    server.use(http.get("/api/admin/servers", () => HttpResponse.json([srv(), srv({ id: 11, name: "de-1", last_error: "timeout" })])));
    renderApp("/admin/servers");
    const row = (await screen.findByText("nl-1")).closest("tr")!;
    expect(within(row).getByText("AmneziaWG")).toBeInTheDocument();
    expect(screen.getByText("timeout")).toBeInTheDocument();
  });

  it("adds a server and follows the import job", async () => {
    adminSession();
    let body: unknown = null;
    server.use(
      http.get("/api/admin/servers", () => HttpResponse.json([])),
      http.post("/api/admin/servers", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ server: srv(), job_id: 5 }, { status: 202 });
      }),
    );
    jobs(["running", "done"]);
    renderApp("/admin/servers");
    await userEvent.click(await screen.findByRole("button", { name: "Добавить сервер" }));
    await userEvent.type(await screen.findByLabelText(/Название/), "nl-1");
    await userEvent.type(screen.getByLabelText(/Адрес сервера/), "203.0.113.10");
    await userEvent.clear(screen.getByLabelText(/SSH-пользователь/));  // prefilled with "root"
    await userEvent.type(screen.getByLabelText(/SSH-пользователь/), "root");
    await userEvent.type(screen.getByLabelText(/SSH-пароль/), "pw");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Добавить сервер" }));
    expect(await screen.findByText("Готово", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(body).toMatchObject({ name: "nl-1", host: "203.0.113.10", ssh_port: 22, ssh_user: "root", ssh_password: "pw" });
  });

  it("shows a connection error when adding", async () => {
    adminSession();
    server.use(
      http.get("/api/admin/servers", () => HttpResponse.json([])),
      http.post("/api/admin/servers", () => err(502, "server_unreachable")),
    );
    renderApp("/admin/servers");
    await userEvent.click(await screen.findByRole("button", { name: "Добавить сервер" }));
    await userEvent.type(await screen.findByLabelText(/Название/), "x");
    await userEvent.type(screen.getByLabelText(/Адрес сервера/), "h");
    await userEvent.clear(screen.getByLabelText(/SSH-пользователь/));  // prefilled with "root"
    await userEvent.type(screen.getByLabelText(/SSH-пользователь/), "root");
    await userEvent.type(screen.getByLabelText(/SSH-пароль/), "pw");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Добавить сервер" }));
    expect(await screen.findByText("Не удалось подключиться к серверу")).toBeInTheDocument();
  });
});

describe("server page", () => {
  function page(s: ServerInfo) {
    adminSession();
    server.use(
      http.get("/api/admin/servers/10", () => HttpResponse.json(s)),
      http.get("/api/admin/servers", () => HttpResponse.json([s])),
      http.get("/api/admin/servers/installable", () =>
        HttpResponse.json([{ container: "amnezia-awg2", title: "AmneziaWG" }, { container: "amnezia-xray", title: "XRay" }])),
    );
  }

  it("syncs and toggles availability", async () => {
    page(srv());
    let patched: unknown = null;
    server.use(
      http.post("/api/admin/servers/10/sync", () => HttpResponse.json({ job_id: 7 }, { status: 202 })),
      http.patch("/api/admin/servers/10", async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json(srv({ enabled_for_users: false }));
      }),
    );
    jobs(["done"]);
    renderApp("/admin/servers/10");
    await userEvent.click(await screen.findByRole("button", { name: "Синхронизировать" }));
    expect(await screen.findByText("Готово", {}, { timeout: 4000 })).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Доступен пользователям"));
    await waitFor(() => expect(patched).toEqual({ enabled_for_users: false }));
  });

  it("offers to accept a changed host key", async () => {
    page(srv({ last_error: "host_key_mismatch: host key of h changed" }));
    let accepted = false;
    server.use(http.post("/api/admin/servers/10/host-key/accept", () => {
      accepted = true;
      return HttpResponse.json({ host_key: "ssh-ed25519 BBBB", job_id: 8 }, { status: 202 });
    }));
    jobs(["done"]);
    renderApp("/admin/servers/10");
    expect(await screen.findByText(/Ключ хоста сервера изменился/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Принять новый ключ" }));
    await userEvent.click(await screen.findByRole("button", { name: "Да" }));
    await waitFor(() => expect(accepted).toBe(true));
  });

  it("asks before reinstalling an existing protocol", async () => {
    page(srv());
    const bodies: unknown[] = [];
    server.use(
      http.post("/api/admin/servers/10/containers", async ({ request }) => {
        const body = (await request.json()) as { force?: boolean };
        bodies.push(body);
        return body.force ? HttpResponse.json({ job_id: 9 }, { status: 202 }) : err(409, "already_installed");
      }),
    );
    jobs(["done"]);
    renderApp("/admin/servers/10");
    await userEvent.click(await screen.findByRole("button", { name: "Установить протокол" }));
    expect(await screen.findByRole("button", { name: "XRay" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "AmneziaWG" }));
    await userEvent.click(await screen.findByRole("button", { name: "Да" }));
    await waitFor(() => expect(bodies).toEqual([
      { container: "amnezia-awg2" },
      { container: "amnezia-awg2", force: true },
    ]));
  });

  it("removes the server from the panel", async () => {
    page(srv());
    let deleted = false;
    server.use(http.delete("/api/admin/servers/10", () => {
      deleted = true;
      return new HttpResponse(null, { status: 204 });
    }));
    const { router } = renderApp("/admin/servers/10");
    await userEvent.click(await screen.findByRole("button", { name: "Удалить" }));
    await userEvent.click(await screen.findByRole("button", { name: "Да" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/servers"));
    expect(deleted).toBe(true);
  });
});

describe("orphans", () => {
  it("assigns an orphan to a user and reports the limit", async () => {
    adminSession();
    const orphan = config({ id: 3, user_id: null, name: "Old phone", can_render: false });
    let attempt = 0;
    server.use(
      http.get("/api/admin/configs", () => HttpResponse.json([orphan])),
      http.get("/api/admin/users", () => HttpResponse.json([{ id: 1, display_name: "Anna", login: "anna" }])),
      http.post("/api/admin/configs/3/assign", async ({ request }) => {
        attempt += 1;
        expect(await request.json()).toEqual({ user_id: 1 });
        return attempt === 1 ? err(409, "config_limit") : HttpResponse.json({ ...orphan, user_id: 1 });
      }),
    );
    renderApp("/admin/orphans");
    const row = (await screen.findByText("Old phone")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Привязать к пользователю" }));
    await userEvent.click(await screen.findByRole("button", { name: /Anna/ }));
    expect(await screen.findByText("Достигнут лимит конфигов")).toBeInTheDocument();
  });
});

describe("audit", () => {
  it("lists entries and loads more", async () => {
    adminSession();
    const urls: string[] = [];
    server.use(
      http.get("/api/admin/audit", ({ request }) => {
        const url = new URL(request.url);
        urls.push(url.search);
        const before = url.searchParams.get("before");
        const entries = before
          ? [{ id: 1, actor: "admin:1", action: "user_create", target: "user:1", details: {}, ts: "2026-09-26T10:00:00Z" }]
          : Array.from({ length: 100 }, (_, i) => ({ id: 200 - i, actor: "admin:1", action: "user_block", target: "user:2", details: {}, ts: "2026-09-26T11:00:00Z" }));
        return HttpResponse.json(entries);
      }),
    );
    renderApp("/admin/audit");
    expect((await screen.findAllByText("user_block")).length).toBe(100);
    await userEvent.click(screen.getByRole("button", { name: "Показать ещё" }));
    expect(await screen.findByText("user_create")).toBeInTheDocument();
    expect(urls.at(-1)).toContain("before=101");
  });
});
