import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  serverExternalPackages: [
    "better-sqlite3",
    "sharp",
    "@imgly/background-removal-node",
    "onnxruntime-node",
  ],
};

export default nextConfig;
