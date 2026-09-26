import { expect, test, type Page } from "@playwright/test";

const ADMIN_PASSWORD = "Adm1n-Pa55w0rd!xyz";
const USER_PASSWORD = "Us3r-Pa55w0rd!qwe";
const NEW_PASSWORD = "N3w-Pa55w0rd!asd";

async function adminLogin(page: Page) {
  await page.goto("/admin/login");
  await page.getByLabel("Логин").fill("admin");
  await page.getByLabel("Пароль").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("heading", { name: "Пользователи" })).toBeVisible();
}

async function ensureServer(page: Page) {
  await page.getByRole("link", { name: "Серверы" }).click();
  const existing = page.getByRole("link", { name: "nl-e2e" });
  await expect(page.getByText("Серверов нет").or(existing.first())).toBeVisible();
  if (await existing.count()) return;
  await page.getByRole("button", { name: "Добавить сервер" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Название").fill("nl-e2e");
  await dialog.getByLabel("Адрес сервера").fill("203.0.113.10");
  await dialog.getByLabel("SSH-пароль").fill("secret");
  await dialog.getByRole("button", { name: "Добавить сервер" }).click();
  await expect(page.getByText("Готово")).toBeVisible({ timeout: 20_000 });
  await expect(existing).toHaveCount(1);
}

async function createUser(page: Page, name: string, expiresOn = ""): Promise<string> {
  await page.getByRole("link", { name: "Пользователи" }).click();
  await page.getByRole("button", { name: "Новый пользователь" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Имя").fill(name);
  if (expiresOn) await dialog.getByLabel("Последний день доступа").fill(expiresOn);
  await dialog.getByRole("button", { name: "Создать" }).click();
  const keyInput = page.getByRole("dialog").getByLabel("Ключ");
  await expect(keyInput).toBeVisible();
  const key = await keyInput.inputValue();
  await page.getByRole("dialog").getByRole("button", { name: "Закрыть" }).click();
  return key;
}

async function logout(page: Page) {
  await page.getByRole("button", { name: "Выйти" }).click();
}

async function register(page: Page, key: string, login: string) {
  await page.goto("/login");
  await page.getByRole("link", { name: "Ввести ключ для авторизации" }).click();
  await page.getByLabel("Ключ").fill(key.toLowerCase().replaceAll("-", " "));
  await page.getByRole("button", { name: "Далее" }).click();
  await page.getByLabel("Логин").fill(login);
  await page.getByLabel(/^Пароль/).fill(USER_PASSWORD);
  await page.getByLabel("Повторите пароль").fill(USER_PASSWORD);
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
  await expect(page.getByRole("heading", { name: "Мои конфиги" })).toBeVisible();
}

test("admin invites a user who manages configs and changes the password", async ({ page }) => {
  await adminLogin(page);
  await ensureServer(page);
  const key = await createUser(page, "Ivan E2E");
  await logout(page);

  await register(page, key, "ivan-e2e");
  await expect(page.getByText("Конфигов: 0 из 3")).toBeVisible();

  await page.getByRole("button", { name: "Создать конфиг" }).first().click();
  const share = page.getByRole("dialog");
  await expect(share.getByLabel("Ключ для приложения AmneziaVPN")).toHaveValue(/^vpn:\/\//);
  await expect(share.locator("svg").first()).toBeVisible();
  // On a phone the QR must stay inside the dialog so it can be scanned.
  await page.setViewportSize({ width: 360, height: 740 });
  const qr = await share.locator(".panel-qr svg").boundingBox();
  const box = await share.locator(".panel-qr").boundingBox();
  expect(qr && box && qr.x >= box.x && qr.x + qr.width <= box.x + box.width + 1).toBeTruthy();
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.keyboard.press("Escape");
  await expect(page.getByText("Конфигов: 1 из 3")).toBeVisible();

  const row = page.getByRole("row").filter({ hasText: "nl-e2e" });
  await row.getByRole("button", { name: "Заблокировать" }).click();
  await page.getByRole("button", { name: "Да", exact: true }).click();
  await expect(row.getByText("Заблокирован вами")).toBeVisible();
  await row.getByRole("button", { name: "Разблокировать" }).click();
  await expect(row.getByText("Активен")).toBeVisible();

  await row.getByRole("button", { name: "Показать" }).click();
  await expect(page.getByRole("dialog").getByLabel("Ключ для приложения AmneziaVPN")).toHaveValue(/^vpn:\/\//);
  await page.keyboard.press("Escape");

  await row.getByRole("button", { name: "Удалить" }).click();
  await page.getByRole("button", { name: "Да", exact: true }).click();
  await expect(page.getByText("Конфигов пока нет")).toBeVisible();

  await page.getByRole("link", { name: "Аккаунт" }).click();
  await page.getByLabel("Текущий пароль").fill(USER_PASSWORD);
  await page.getByLabel("Новый пароль").fill(NEW_PASSWORD);
  await page.getByLabel("Повторите пароль").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Сохранить" }).click();
  await expect(page.getByText(/Пароль изменён/)).toBeVisible();
  await logout(page);

  await page.getByLabel("Логин").fill("ivan-e2e");
  await page.getByLabel("Пароль").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("heading", { name: "Мои конфиги" })).toBeVisible();
});

test("a user whose access has ended sees why", async ({ page }) => {
  await adminLogin(page);
  await ensureServer(page);
  const key = await createUser(page, "Expired E2E", "2026-01-01");
  await logout(page);
  await register(page, key, "expired-e2e");
  await expect(page.getByText(/Срок доступа истёк/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Создать конфиг" }).first()).toBeDisabled();
});

test("admin blocks a user and the user sees it", async ({ page }) => {
  await adminLogin(page);
  await ensureServer(page);
  const key = await createUser(page, "Blocked E2E");
  await logout(page);
  await register(page, key, "blocked-e2e");
  await page.getByRole("button", { name: "Создать конфиг" }).first().click();
  await page.keyboard.press("Escape");
  await logout(page);

  await adminLogin(page);
  await page.getByRole("link", { name: "Blocked E2E" }).click();
  await page.getByRole("button", { name: "Заблокировать" }).first().click();
  await page.getByRole("button", { name: "Да", exact: true }).click();
  await expect(page.getByText("Заблокирован").first()).toBeVisible();
  await logout(page);

  await page.goto("/login");
  await page.getByLabel("Логин").fill("blocked-e2e");
  await page.getByLabel("Пароль").fill(USER_PASSWORD);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByText(/Доступ заблокирован администратором/)).toBeVisible();
});
