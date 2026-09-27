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
        <LoginForm />
        <Anchor component={Link} to="/invite" ta="center" size="sm">
          {t("auth.have_key")}
        </Anchor>
      </Stack>
    </AuthLayout>
  );
}
