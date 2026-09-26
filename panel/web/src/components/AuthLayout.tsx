import { Center, Group, Paper, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { LanguageSwitch } from "./AppLayout";

export function AuthLayout({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <Center mih="100vh" p="md">
      <Stack w="100%" maw={420}>
        <Group justify="space-between">
          <Text fw={700}>{t("app.title")}</Text>
          <LanguageSwitch />
        </Group>
        <Paper withBorder shadow="sm" p="lg" radius="md">
          <Title order={2} mb="md">
            {title}
          </Title>
          {children}
        </Paper>
      </Stack>
    </Center>
  );
}
