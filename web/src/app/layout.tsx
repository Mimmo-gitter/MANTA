import type { Metadata } from "next";
import "./globals.css";
import { QueryProvider } from "@/providers/query-provider";
import { AuthProvider } from "@/providers/auth-provider";
import {
  getServerRuntimeConfig,
  serializeRuntimeConfigScript,
} from "@/lib/runtime-config";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "MANTA — Engineering Intelligence Platform",
  description: "Corporate platform for delivery intelligence, DORA metrics, and verified engineering governance."
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  const runtimeConfigScript = serializeRuntimeConfigScript(
    getServerRuntimeConfig(),
  );

  return (
    <html lang="en" className="dark h-full">
      <head>
        <script
          id="manta-runtime-config"
          dangerouslySetInnerHTML={{ __html: runtimeConfigScript }}
        />
      </head>
      <body className="min-h-full antialiased" suppressHydrationWarning>
        <QueryProvider>
          <AuthProvider>{children}</AuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
