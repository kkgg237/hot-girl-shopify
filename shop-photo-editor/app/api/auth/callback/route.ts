import { NextRequest, NextResponse } from "next/server";
import crypto from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

// Handles the Shopify OAuth callback. Exchanges the code for an access token
// and writes it into .env so the rest of the app can use it.
export async function GET(req: NextRequest) {
  const params = req.nextUrl.searchParams;
  const code = params.get("code");
  const shop = params.get("shop");
  const state = params.get("state");
  const hmac = params.get("hmac");

  const apiKey = process.env.SHOPIFY_API_KEY;
  const apiSecret = process.env.SHOPIFY_API_SECRET;
  if (!apiKey || !apiSecret) {
    return new NextResponse("Missing SHOPIFY_API_KEY or SHOPIFY_API_SECRET in .env", { status: 400 });
  }
  if (!code || !shop || !state || !hmac) {
    return new NextResponse("Missing OAuth params", { status: 400 });
  }

  // Verify state cookie
  const stateCookie = req.cookies.get("shopify_oauth_state")?.value;
  if (!stateCookie || stateCookie !== state) {
    return new NextResponse("Invalid OAuth state", { status: 400 });
  }

  // Verify HMAC
  const sortedParams = Array.from(params.entries())
    .filter(([k]) => k !== "hmac" && k !== "signature")
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join("&");
  const expectedHmac = crypto.createHmac("sha256", apiSecret).update(sortedParams).digest("hex");
  if (!crypto.timingSafeEqual(Buffer.from(expectedHmac, "hex"), Buffer.from(hmac, "hex"))) {
    return new NextResponse("Invalid HMAC", { status: 400 });
  }

  // Exchange code for access token
  const tokenRes = await fetch(`https://${shop}/admin/oauth/access_token`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ client_id: apiKey, client_secret: apiSecret, code }),
  });
  if (!tokenRes.ok) {
    const text = await tokenRes.text();
    return new NextResponse(`Token exchange failed ${tokenRes.status}: ${text}`, { status: 500 });
  }
  const data = (await tokenRes.json()) as { access_token: string; scope: string };
  const token = data.access_token;

  // Persist to .env (replace existing SHOPIFY_ADMIN_TOKEN line)
  const envPath = path.resolve(process.cwd(), ".env");
  let env = "";
  try {
    env = await readFile(envPath, "utf8");
  } catch {
    env = "";
  }
  const lines = env.split("\n");
  let found = false;
  const updated = lines.map((l) => {
    if (l.startsWith("SHOPIFY_ADMIN_TOKEN=")) {
      found = true;
      return `SHOPIFY_ADMIN_TOKEN=${token}`;
    }
    if (l.startsWith("SHOPIFY_STORE_DOMAIN=") && shop) {
      return `SHOPIFY_STORE_DOMAIN=${shop}`;
    }
    return l;
  });
  if (!found) updated.push(`SHOPIFY_ADMIN_TOKEN=${token}`);
  await writeFile(envPath, updated.join("\n"), "utf8");

  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Shopify connected</title>
  <style>body{font:16px/1.5 -apple-system,system-ui,sans-serif;max-width:560px;margin:80px auto;padding:0 24px;color:#0a0a0a}
  .ok{display:inline-block;background:#ecfdf5;color:#065f46;padding:6px 12px;border-radius:999px;font-size:13px;font-weight:600;border:1px solid #a7f3d0}
  code{background:#f4f4f5;padding:2px 6px;border-radius:4px;font-size:13px}
  a.btn{display:inline-block;margin-top:24px;background:#0a0a0a;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600}</style>
  </head><body>
  <span class="ok">✓ Connected</span>
  <h1>Shopify connected to <code>${shop}</code></h1>
  <p>Access token saved to <code>.env</code> as <code>SHOPIFY_ADMIN_TOKEN</code>.</p>
  <p>Granted scopes: <code>${data.scope}</code></p>
  <p><strong>Restart the dev server</strong> (Ctrl+C, then <code>npm run dev</code>) for the token to take effect.</p>
  <a class="btn" href="/">Open the editor →</a>
  </body></html>`;
  return new NextResponse(html, { headers: { "content-type": "text/html" } });
}
