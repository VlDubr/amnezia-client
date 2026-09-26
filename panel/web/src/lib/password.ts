import { ZxcvbnFactory } from "@zxcvbn-ts/core";
import * as common from "@zxcvbn-ts/language-common";

const zxcvbn = new ZxcvbnFactory({ graphs: common.adjacencyGraphs, dictionary: { ...common.dictionary } });

export const MIN_LENGTH = 12;
const MIN_SCORE = 3;

/** Client-side hint mirroring the server policy (panel/backend/app/security/passwords.py). */
export function passwordProblem(password: string, userInputs: string[] = []): "too_short" | "too_weak" | null {
  if (password.length < MIN_LENGTH) return "too_short";
  return zxcvbn.check(password.slice(0, 100), userInputs).score < MIN_SCORE ? "too_weak" : null;
}
