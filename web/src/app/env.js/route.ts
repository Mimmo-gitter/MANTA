import {
  getServerRuntimeConfig,
  serializeRuntimeConfigScript,
} from "@/lib/runtime-config";

export const dynamic = "force-dynamic";

export function GET(): Response {
  const config = getServerRuntimeConfig();
  const body = serializeRuntimeConfigScript(config);

  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "application/javascript; charset=utf-8",
      "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
