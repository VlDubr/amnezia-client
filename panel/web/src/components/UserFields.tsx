import { NumberInput, Textarea, TextInput } from "@mantine/core";
import { useTranslation } from "react-i18next";

export type UserFieldValues = { display_name: string; note: string; max_configs: number | ""; expires_on: string };

export const EMPTY_USER: UserFieldValues = { display_name: "", note: "", max_configs: 3, expires_on: "" };

/** Name, config limit (А.6), last day of access (А.5) and a note. */
export function UserFields({ value, onChange }: { value: UserFieldValues; onChange: (v: UserFieldValues) => void }) {
  const { t } = useTranslation();
  const set = (patch: Partial<UserFieldValues>) => onChange({ ...value, ...patch });
  return (
    <>
      <TextInput
        label={t("admin.display_name")}
        value={value.display_name}
        onChange={(e) => set({ display_name: e.currentTarget.value })}
        required
        maxLength={128}
      />
      <NumberInput
        label={t("admin.max_configs")}
        value={value.max_configs}
        // A cleared field stays empty, so the required check stops the form instead of saving 0.
        onChange={(v) => set({ max_configs: typeof v === "number" ? v : "" })}
        min={0}
        max={1000}
        allowDecimal={false}
        required
      />
      <TextInput
        type="date"
        label={t("admin.expires_on")}
        description={t("admin.expires_hint")}
        value={value.expires_on}
        onChange={(e) => set({ expires_on: e.currentTarget.value })}
      />
      <Textarea label={t("admin.note")} value={value.note} onChange={(e) => set({ note: e.currentTarget.value })} autosize minRows={2} />
    </>
  );
}

export function userBody(v: UserFieldValues) {
  return { display_name: v.display_name, note: v.note, max_configs: v.max_configs, expires_on: v.expires_on || null };
}
