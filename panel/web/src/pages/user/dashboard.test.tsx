import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { EXPORT, config, err, me, userSession } from "../../../test/fixtures";
import { server } from "../../../test/msw";
import { renderApp } from "../../../test/render";

describe("user dashboard", () => {
  it("shows each server's load in the order given and marks the recommended one", async () => {
    userSession(me(), [], [
      { id: 11, name: "de-1", containers: [{ container: "amnezia-awg2", title: "AmneziaWG" }], load: "low", recommended: true },
      { id: 10, name: "nl-1", containers: [{ container: "amnezia-awg2", title: "AmneziaWG" }], load: "high", recommended: false },
      { id: 12, name: "fi-1", containers: [{ container: "amnezia-awg2", title: "AmneziaWG" }], load: "unknown", recommended: false },
    ]);
    renderApp("/");
    const cards = await screen.findAllByTestId("server-card");
    expect(cards.map((c) => within(c).getByTestId("server-name").textContent)).toEqual(["de-1", "nl-1", "fi-1"]);
    expect(within(cards[0]).getByText("Низкая нагрузка")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Рекомендуем")).toBeInTheDocument();
    expect(within(cards[1]).getByText("Высокая нагрузка")).toBeInTheDocument();
    expect(within(cards[2]).getByText("Нет данных о нагрузке")).toBeInTheDocument();
    expect(screen.getAllByText("Рекомендуем")).toHaveLength(1);
  });

  it("shows access period, limit usage and configs", async () => {
    userSession(me(), [config()]);
    renderApp("/");
    expect(await screen.findByText("Доступ до 31.12.2026")).toBeInTheDocument();
    expect(screen.getByText("Конфигов: 1 из 3")).toBeInTheDocument();
    const row = (await screen.findByText("Phone")).closest("tr")!;
    expect(within(row).getByText("nl-1")).toBeInTheDocument();
    expect(within(row).getByText("Активен")).toBeInTheDocument();
  });

  it("creates a config and opens it for sharing", async () => {
    let configs = [config()];
    userSession(me(), configs);
    server.use(
      http.get("/api/me/configs", () => HttpResponse.json(configs)),
      http.post("/api/me/configs", async ({ request }) => {
        expect(await request.json()).toEqual({ server_id: 10, container: "amnezia-awg2" });
        configs = [...configs, config({ id: 2, name: "nl-1 AmneziaWG" })];
        return HttpResponse.json(configs[1], { status: 201 });
      }),
      http.get("/api/me/configs/2", () => HttpResponse.json({ ...configs[1], export: EXPORT })),
    );
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: /Создать конфиг/ }));
    expect(await screen.findByDisplayValue("vpn://KEY")).toBeInTheDocument();
    expect(await screen.findByText("nl-1 AmneziaWG")).toBeInTheDocument();
  });

  it("explains a failed creation and keeps the list", async () => {
    userSession(me(), [config()]);
    server.use(http.post("/api/me/configs", () => err(409, "config_limit")));
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: /Создать конфиг/ }));
    expect(await screen.findByText("Достигнут лимит конфигов")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(2);
  });

  it("reports an unavailable server", async () => {
    userSession(me(), []);
    server.use(http.post("/api/me/configs", () => err(503, "server_unavailable")));
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: /Создать конфиг/ }));
    expect(await screen.findByText("Сервер не отвечает. Попробуйте позже.")).toBeInTheDocument();
  });

  it("disables creation at the limit", async () => {
    userSession(me({ configs_count: 3 }), [config()]);
    renderApp("/");
    expect(await screen.findByRole("button", { name: /Создать конфиг/ })).toBeDisabled();
  });

  it("shows, blocks, unblocks and deletes a config", async () => {
    const calls: string[] = [];
    userSession(me(), [config()]);
    server.use(
      http.get("/api/me/configs/1", () => HttpResponse.json({ ...config(), export: EXPORT })),
      http.post("/api/me/configs/1/block", () => {
        calls.push("block");
        return HttpResponse.json(config({ status: "blocked", blocked_by: "user" }));
      }),
      http.delete("/api/me/configs/1", () => {
        calls.push("delete");
        return HttpResponse.json({ id: 1 }, { status: 202 });
      }),
    );
    renderApp("/");
    const row = (await screen.findByText("Phone")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Показать" }));
    expect(await screen.findByDisplayValue("vpn://KEY")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");

    await userEvent.click(within(row).getByRole("button", { name: "Заблокировать" }));
    await userEvent.click(await screen.findByRole("button", { name: "Да" }));
    await waitFor(() => expect(calls).toEqual(["block"]));

    await userEvent.click(within(row).getByRole("button", { name: "Удалить" }));
    await userEvent.click(await screen.findByRole("button", { name: "Да" }));
    await waitFor(() => expect(calls).toEqual(["block", "delete"]));
  });

  it("lets the user unblock only their own blocks", async () => {
    userSession(me(), [config({ status: "blocked", blocked_by: "admin" }), config({ id: 2, name: "Laptop", status: "blocked", blocked_by: "user" })]);
    renderApp("/");
    const adminRow = (await screen.findByText("Phone")).closest("tr")!;
    expect(within(adminRow).getByText("Заблокирован администратором")).toBeInTheDocument();
    expect(within(adminRow).queryByRole("button", { name: "Разблокировать" })).toBeNull();
    const ownRow = screen.getByText("Laptop").closest("tr")!;
    expect(within(ownRow).getByRole("button", { name: "Разблокировать" })).toBeEnabled();
  });

  it("warns a blocked user and disables actions that would fail", async () => {
    userSession(me({ status: "blocked" }), [config({ status: "inactive" })]);
    renderApp("/");
    expect(await screen.findByText(/Доступ заблокирован администратором/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Создать конфиг/ })).toBeDisabled();
    const row = screen.getByText("Phone").closest("tr")!;
    expect(within(row).getByRole("button", { name: "Показать" })).toBeDisabled();
  });

  it("warns an expired user", async () => {
    userSession(me({ status: "expired" }), []);
    renderApp("/");
    expect(await screen.findByText(/Срок доступа истёк/)).toBeInTheDocument();
  });
});

describe("account page", () => {
  it("changes the password", async () => {
    userSession(me(), []);
    let body: unknown = null;
    server.use(
      http.post("/api/me/password", async ({ request }) => {
        body = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/account");
    await userEvent.type(await screen.findByLabelText(/Текущий пароль/), "old-password");
    await userEvent.type(screen.getByLabelText(/^Новый пароль/), "Vq7#mZ2!rT9p@Lx");
    await userEvent.type(screen.getByLabelText(/Повторите пароль/), "Vq7#mZ2!rT9p@Lx");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText(/Пароль изменён/)).toBeInTheDocument();
    expect(body).toEqual({ old: "old-password", new: "Vq7#mZ2!rT9p@Lx" });
  });

  it("shows a wrong current password", async () => {
    userSession(me(), []);
    server.use(http.post("/api/me/password", () => err(403, "wrong_password")));
    renderApp("/account");
    await userEvent.type(await screen.findByLabelText(/Текущий пароль/), "wrong");
    await userEvent.type(screen.getByLabelText(/^Новый пароль/), "Vq7#mZ2!rT9p@Lx");
    await userEvent.type(screen.getByLabelText(/Повторите пароль/), "Vq7#mZ2!rT9p@Lx");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Текущий пароль указан неверно");
  });
});
