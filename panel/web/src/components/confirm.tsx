import { Text } from "@mantine/core";
import { modals } from "@mantine/modals";
import i18n from "../i18n";

/** Asks for confirmation before a destructive action. */
export function confirmAction(message: string, onConfirm: () => void, danger = true) {
  modals.openConfirmModal({
    title: i18n.t("common.confirm"),
    children: <Text size="sm">{message}</Text>,
    labels: { confirm: i18n.t("common.yes"), cancel: i18n.t("common.cancel") },
    confirmProps: { color: danger ? "red" : undefined },
    onConfirm,
  });
}
