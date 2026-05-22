import { NextRequest, NextResponse } from "next/server";
import crypto from "node:crypto";

// Kicks off Shopify OAuth. Visit /api/auth/install in your browser to start.
export async function GET(req: NextRequest) {
  const shop = req.nextUrl.searchParams.get("shop") ?? process.env.SHOPIFY_STORE_DOMAIN;
  const apiKey = process.env.SHOPIFY_API_KEY;
  if (!shop || !apiKey) {
    return new NextResponse(
      "Missing SHOPIFY_STORE_DOMAIN or SHOPIFY_API_KEY in .env",
      { status: 400 },
    );
  }
  const scopes = [
    "read_products",
    "write_products",
    "read_files",
    "write_files",
  ].join(",");
  const state = crypto.randomBytes(16).toString("hex");
  const redirectUri = `${req.nextUrl.origin}/api/auth/callback`;
  const url = new URL(`https://${shop}/admin/oauth/authorize`);
  url.searchParams.set("client_id", apiKey);
  url.searchParams.set("scope", scopes);
  url.searchParams.set("redirect_uri", redirectUri);
  url.searchParams.set("state", state);

  const res = NextResponse.redirect(url.toString());
  res.cookies.set("shopify_oauth_state", state, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 600,
  });
  return res;
}
