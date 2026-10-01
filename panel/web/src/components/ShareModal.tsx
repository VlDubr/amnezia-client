import { Alert, Anchor, Box, Button, Code, CopyButton, Group, Modal, Stack, Text } from "@mantine/core";
import { useTranslation } from "react-i18next";
import type { Export } from "../api/types";
import { CopyField } from "./CopyField";

type Props = { opened: boolean; name: string; data: Export | null | undefined; onClose: () => void };

/** One downloadable config file: its text, a copy button and a download link. */
function FileBlock({ label, filename, content }: { label: string; filename: string; content: string }) {
  const { t } = useTranslation();
  const type = filename.endsWith(".json") ? "application/json" : "text/plain";
  return (
    <Box>
      <Group justify="space-between" mb={4}>
        <Text size="sm" fw={500}>
          {label}
        </Text>
        <Group gap="xs">
          <CopyButton value={content}>
            {({ copied, copy }) => (
              <Button size="xs" variant="light" onClick={copy}>
                {copied ? t("common.copied") : t("common.copy")}
              </Button>
            )}
          </CopyButton>
          <Anchor href={`data:${type};charset=utf-8,${encodeURIComponent(content)}`} download={filename} size="sm">
            {t("common.download")}
          </Anchor>
        </Group>
      </Group>
      <Code block style={{ maxHeight: 220, overflow: "auto", whiteSpace: "pre" }}>
        {content}
      </Code>
    </Box>
  );
}

/** Shows a config the way the Qt client's share page does: vpn:// key, QR code and the native config files. */
export function ShareModal({ opened, name, data, onClose }: Props) {
  const { t } = useTranslation();
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
          {data.vpn_key && <FileBlock label={t("share.native")} filename={data.native_filename} content={data.native} />}
          {(data.files ?? []).map((f) => (
            <FileBlock
              key={f.filename}
              label={t(`share.file.${f.kind}`, { defaultValue: f.filename })}
              filename={f.filename}
              content={f.content}
            />
          ))}
        </Stack>
      )}
    </Modal>
  );
}
