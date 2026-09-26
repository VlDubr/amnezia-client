import { Title } from "@mantine/core";
import { useTranslation } from "react-i18next";

export default function AccountPage() {
  const { t } = useTranslation();
  return <Title order={2}>{t("account.title")}</Title>;
}
