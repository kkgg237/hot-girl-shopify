import { sqlite } from "@/db";

export const DEFAULT_PROMPT_RULES = `1. NEVER alter the product itself (clothing, bag, shoes, accessories, jewelry). Preserve every pixel of every fold, seam, stitch, weave, label, hardware, color, texture, drape, and shadow ON the product. The product must look identical.
2. NEVER alter the model (if present): face, skin, hair, body, pose, proportions, fingernails, tattoos, accessories worn — all unchanged.
3. NEVER restyle, recolor, retouch, or "enhance" the product or model.
4. ONLY modify the BACKGROUND — the area BEHIND and AROUND the subject:
   - Even out uneven lighting on the background so it's uniform.
   - Remove dirt, dust, scuffs, lint, hair, marks, shadows on the floor or wall (only those NOT cast by the product/model itself — keep grounding shadows under the subject).
   - Match the background color/tone to the references (typically clean white seamless).
5. If you cannot tell whether something is part of the product or the background, LEAVE IT ALONE.
6. Do not add props, text, logos, or graphics. Do not change the composition.`;

export interface AppSettings {
  houseStyleImageUrls: string[];
  promptRules: string;
}

interface SettingsRow {
  id: string;
  house_style_image_urls: string;
  prompt_rules: string | null;
}

export function getSettings(): AppSettings {
  const row = sqlite
    .prepare(
      "SELECT id, house_style_image_urls, prompt_rules FROM app_settings WHERE id = 'singleton'"
    )
    .get() as SettingsRow | undefined;
  if (!row) return { houseStyleImageUrls: [], promptRules: DEFAULT_PROMPT_RULES };
  let urls: string[] = [];
  try {
    const parsed = JSON.parse(row.house_style_image_urls);
    if (Array.isArray(parsed)) {
      urls = parsed.filter((x): x is string => typeof x === "string");
    }
  } catch {
    urls = [];
  }
  return {
    houseStyleImageUrls: urls,
    promptRules: row.prompt_rules && row.prompt_rules.trim().length > 0 ? row.prompt_rules : DEFAULT_PROMPT_RULES,
  };
}

export function setHouseStyle(urls: string[]): void {
  const json = JSON.stringify(urls);
  sqlite
    .prepare(
      `INSERT INTO app_settings(id, house_style_image_urls) VALUES('singleton', ?) ON CONFLICT(id) DO UPDATE SET house_style_image_urls=excluded.house_style_image_urls`
    )
    .run(json);
}

export function setPromptRules(rules: string): void {
  sqlite
    .prepare(
      `INSERT INTO app_settings(id, prompt_rules) VALUES('singleton', ?) ON CONFLICT(id) DO UPDATE SET prompt_rules=excluded.prompt_rules`
    )
    .run(rules);
}
