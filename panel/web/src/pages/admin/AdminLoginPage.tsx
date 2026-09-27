import { Anchor, Stack } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { AuthLayout } from "../../components/AuthLayout";
import { LoginForm } from "../../components/LoginForm";

export default function AdminLoginPage() {
  const { t } = useTranslation();
  return (
    <AuthLayout title={t("auth.admin_login_title")}>
      <Stack>
        <LoginForm role="admin" />
        <Anchor component={Link} to="/login" ta="center" size="sm">
          {t("auth.user_link")}
        </Anchor>
      </Stack>
    </AuthLayout>
  );
}
