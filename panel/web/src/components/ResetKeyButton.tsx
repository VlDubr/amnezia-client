import { Alert, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import type { ResetKey } from "../api/types";
import { formatDateTime } from "../lib/format";
import { confirmAction } from "./confirm";
import { CopyField } from "./CopyField";
import { ErrorAlert } from "./ErrorAlert";

/** Issues a one-time password reset key; the account's password changes only when the key is used. */
export function ResetKeyButton({ accountId, name }: { accountId: number; name: string }) {
  const { t, i18n } = useTranslation();
  const [issued, setIssued] = useState<ResetKey | null>(null);
  const issue = useMutation({
    mutationFn: () => api<ResetKey>(`/api/admin/accounts/${accountId}/reset-key`, { method: "POST" }),
    onSuccess: setIssued,
  });

  return (
    <>
      <Button
        variant="light"
        loading={issue.isPending}
        onClick={() => confirmAction(t("admin.reset_key_confirm", { name }), () => issue.mutate(), false)}
      >
        {t("admin.reset_key")}
      </Button>
      <Modal
        opened={issued !== null || issue.isError}
        onClose={() => {
          setIssued(null);
          issue.reset();
        }}
        title={t("admin.reset_key")}
        closeOnClickOutside={false}
      >
        <ErrorAlert error={issue.error} />
        {issued && (
          <Stack>
            <Alert color="yellow">{t("admin.reset_key_once")}</Alert>
            <CopyField label={t("auth.invite_key")} value={issued.reset_key} />
            <Text size="sm" c="dimmed">
              {t("admin.reset_key_until", { date: formatDateTime(issued.expires_at, i18n.language) })}
            </Text>
            <Group justify="flex-end">
              <Button onClick={() => setIssued(null)}>{t("common.close")}</Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </>
  );
}
