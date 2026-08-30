import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  output: "export",
  distDir: path.join(process.cwd(), "..", "app", "static"),
  basePath: "",
};

export default nextConfig;
