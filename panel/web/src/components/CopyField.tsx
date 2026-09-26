import { ActionIcon, CopyButton, TextInput, Tooltip } from "@mantine/core";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";

export function CopyField({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation();
  return (
    <TextInput
      label={label}
      value={value}
      readOnly
      onFocus={(e) => e.currentTarget.select()}
      rightSection={
        <CopyButton value={value}>
          {({ copied, copy }) => (
            <Tooltip label={copied ? t("common.copied") : t("common.copy")} withArrow>
              <ActionIcon variant="subtle" color={copied ? "teal" : "gray"} onClick={copy} aria-label={t("common.copy")}>
                {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
              </ActionIcon>
            </Tooltip>
          )}
        </CopyButton>
      }
    />
  );
}
