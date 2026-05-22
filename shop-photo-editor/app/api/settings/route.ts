import { getSettings, DEFAULT_PROMPT_RULES } from "@/lib/settings";

export async function GET() {
  const s = getSettings();
  return Response.json({
    houseStyleImageUrls: s.houseStyleImageUrls,
    promptRules: s.promptRules,
    defaultPromptRules: DEFAULT_PROMPT_RULES,
  });
}
