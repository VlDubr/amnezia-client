import { Alert, Button, Group, Modal, Stack } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { CopyField } from "./CopyField";

/** А.1: the invite key is shown once; after closing it can only be reissued. */
export function InviteKeyModal({ inviteKey, onClose }: { inviteKey: string | null; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <Modal opened={inviteKey !== null} onClose={onClose} title={t("admin.invite_title")} closeOnClickOutside={false}>
      {inviteKey && (
        <Stack>
          <Alert color="yellow">{t("admin.invite_once")}</Alert>
          <CopyField label={t("auth.invite_key")} value={inviteKey} />
          <Group justify="flex-end">
            <Button onClick={onClose}>{t("common.close")}</Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
