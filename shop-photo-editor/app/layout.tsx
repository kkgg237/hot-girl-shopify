import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";
import { TopNav } from "@/components/top-nav";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Photo Editor",
  description: "Shopify product photo editor powered by Gemini.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col bg-neutral-50 text-neutral-900">
        <TopNav />
        <main className="flex-1 flex flex-col">{children}</main>
        <Toaster richColors closeButton position="bottom-right" />
      </body>
    </html>
  );
}
