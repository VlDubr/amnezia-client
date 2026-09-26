import { useTranslation } from "react-i18next";
import { AuthLayout } from "../../components/AuthLayout";
import { LoginForm } from "../../components/LoginForm";

export default function AdminLoginPage() {
  const { t } = useTranslation();
  return (
    <AuthLayout title={t("auth.admin_login_title")}>
      <LoginForm role="admin" />
    </AuthLayout>
  );
}
