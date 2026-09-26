import { Title } from "@mantine/core";
import { useTranslation } from "react-i18next";

export default function AdminLoginPage() {
  const { t } = useTranslation();
  return <Title order={2}>{t("auth.admin_login_title")}</Title>;
}
