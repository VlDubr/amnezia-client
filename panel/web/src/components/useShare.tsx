import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { api } from "../api/client";
import type { Config } from "../api/types";
import { errorText } from "../i18n";
import { ShareModal } from "./ShareModal";

/** Loads a config with its export and shows it in the share dialog (А.2 / Б.3: issue the same config again). */
export function useShare(basePath: "/api/me/configs" | "/api/admin/configs") {
  const [shown, setShown] = useState<Config | null>(null);

  async function open(id: number) {
    try {
      setShown(await api<Config>(`${basePath}/${id}`));
    } catch (e) {
      notifications.show({ color: "red", message: errorText(e) });
    }
  }

  const modal = (
    <ShareModal opened={shown !== null} name={shown?.name ?? ""} data={shown?.export} onClose={() => setShown(null)} />
  );
  return { open, modal };
}
