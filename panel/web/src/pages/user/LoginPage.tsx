import { Anchor, Stack } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { AuthLayout } from "../../components/AuthLayout";
import { LoginForm } from "../../components/LoginForm";

export default function LoginPage() {
  const { t } = useTranslation();
  return (
    <AuthLayout title={t("auth.login_title")}>
      <Stack>
        <LoginForm role="user" />
        <Anchor component={Link} to="/invite" ta="center" size="sm">
          {t("auth.have_key")}
        </Anchor>
        {/* Administrators sign in on their own page: the same login here is checked against users only. */}
        <Anchor component={Link} to="/admin/login" ta="center" size="sm" c="dimmed">
          {t("auth.admin_link")}
        </Anchor>
      </Stack>
    </AuthLayout>
  );
}
