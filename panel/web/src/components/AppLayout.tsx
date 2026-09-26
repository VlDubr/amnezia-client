import { AppShell, Burger, Button, Group, NavLink, SegmentedControl, Stack, Text, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { NavLink as RouterLink, Outlet, useNavigate } from "react-router";
import { api } from "../api/client";
import type { Role } from "../api/types";
import { SESSION_KEY, loginPath, useSession } from "../auth/session";

const ADMIN_LINKS = [
  { to: "/admin/users", label: "admin.users" },
  { to: "/admin/servers", label: "admin.servers" },
  { to: "/admin/orphans", label: "admin.orphans" },
  { to: "/admin/audit", label: "admin.audit" },
];

export function LanguageSwitch() {
  const { i18n } = useTranslation();
  return (
    <SegmentedControl
      size="xs"
      value={i18n.language}
      onChange={(lng) => void i18n.changeLanguage(lng)}
      data={[
        { value: "ru", label: "RU" },
        { value: "en", label: "EN" },
      ]}
    />
  );
}

export function AppLayout({ area }: { area: Role }) {
  const { t } = useTranslation();
  const [opened, { toggle, close }] = useDisclosure();
  const { data: session } = useSession();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    queryClient.clear();
    queryClient.setQueryData(SESSION_KEY, null);
    navigate(loginPath(area));
  }

  const links =
    area === "admin" ? ADMIN_LINKS : [{ to: "/", label: "dashboard.title" }, { to: "/account", label: "account.title" }];

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 220, breakpoint: "sm", collapsed: { mobile: !opened } }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="menu" />
            <Title order={4}>{t("app.title")}</Title>
            <Text c="dimmed" size="sm" visibleFrom="xs">
              {area === "admin" ? t("app.admin") : t("app.cabinet")}
            </Text>
          </Group>
          <Group gap="sm" wrap="nowrap">
            <LanguageSwitch />
            <Text size="sm" visibleFrom="sm">
              {session?.login}
            </Text>
            <Button size="xs" variant="subtle" onClick={() => void logout()}>
              {t("app.logout")}
            </Button>
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="xs">
        <Stack gap={2}>
          {links.map((l) => (
            <NavLink key={l.to} component={RouterLink} to={l.to} end label={t(l.label)} onClick={close} />
          ))}
        </Stack>
      </AppShell.Navbar>
      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
