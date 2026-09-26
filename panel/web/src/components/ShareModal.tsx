import { Alert, Anchor, Box, Button, Code, CopyButton, Group, Modal, Stack, Text } from "@mantine/core";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { Export } from "../api/types";
import { CopyField } from "./CopyField";

type Props = { opened: boolean; name: string; data: Export | null | undefined; onClose: () => void };

/** Shows a config the way the Qt client's share page does: vpn:// key, QR code and the native config file. */
export function ShareModal({ opened, name, data, onClose }: Props) {
  const { t } = useTranslation();
  const downloadUrl = useMemo(
    () => (data ? `data:text/plain;charset=utf-8,${encodeURIComponent(data.native)}` : ""),
    [data],
  );
  const qrIsSvg = !!data && data.qr_svg.trimStart().startsWith("<svg");

  return (
    <Modal opened={opened} onClose={onClose} title={t("share.title", { name })} size="lg">
      {!data ? (
        <Alert color="yellow">{t("share.unavailable")}</Alert>
      ) : (
        <Stack>
          <Text size="sm" c="dimmed">
            {data.vpn_key ? t("share.how") : t("share.how_link")}
          </Text>
          {data.vpn_key ? (
            <CopyField label={t("share.vpn_key")} value={data.vpn_key} />
          ) : (
            <CopyField label={t("share.link")} value={data.native} />
          )}
          {qrIsSvg && (
            <Box>
              <Text size="sm" fw={500} mb={4}>
                {t("share.qr")}
              </Text>
              <Box
                className="panel-qr"
                maw={280}
                mx="auto"
                bg="white"
                p="xs"
                style={{ borderRadius: 8 }}
                // Backend-generated SVG (segno); rendered only when it is an SVG document.
                dangerouslySetInnerHTML={{ __html: data.qr_svg }}
              />
            </Box>
          )}
          {data.vpn_key && (
          <Box>
            <Group justify="space-between" mb={4}>
              <Text size="sm" fw={500}>
                {t("share.native")}
              </Text>
              <Group gap="xs">
                <CopyButton value={data.native}>
                  {({ copied, copy }) => (
                    <Button size="xs" variant="light" onClick={copy}>
                      {copied ? t("common.copied") : t("common.copy")}
                    </Button>
                  )}
                </CopyButton>
                <Anchor href={downloadUrl} download={data.native_filename} size="sm">
                  {t("common.download")}
                </Anchor>
              </Group>
            </Group>
            <Code block style={{ maxHeight: 220, overflow: "auto", whiteSpace: "pre" }}>
              {data.native}
            </Code>
          </Box>
          )}
        </Stack>
      )}
    </Modal>
  );
}
