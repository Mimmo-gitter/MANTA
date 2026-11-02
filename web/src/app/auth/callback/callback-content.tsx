"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";
import { handleSigninCallback } from "@/lib/auth";
import { useAuth } from "@/providers/auth-provider";

export function CallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { refresh } = useAuth();
  const [statusMessage, setStatusMessage] = useState("Validating security tokens...");
  const hasExecutedRef = useRef(false);

  useEffect(() => {
    // Prevent React 19 / Next.js StrictMode double invocation
    if (hasExecutedRef.current) return;
    hasExecutedRef.current = true;

    async function processCallback() {
      try {
        // 1. Check if upstream IdP reported an error
        const idpError = searchParams.get("error");
        const idpErrorDesc = searchParams.get("error_description");
        if (idpError) {
          throw new Error(idpErrorDesc || `Identity provider reported error: ${idpError}`);
        }

        // 2. Validate presence of code and state query parameters
        const stateParam = searchParams.get("state");
        const codeParam = searchParams.get("code");
        if (!stateParam) {
          throw new Error("Missing state parameter in callback redirect");
        }
        if (!codeParam) {
          throw new Error("Missing authorization code in callback redirect");
        }

        setStatusMessage("Exchanging authorization code for cryptographic tokens...");

        // 3. Complete Authorization Code with PKCE and validate state/nonce/ID token
        const user = await handleSigninCallback();
        if (!user || user.expired) {
          throw new Error("Token exchange produced an invalid or expired user session");
        }

        setStatusMessage("Synchronizing user claims...");
        await refresh();

        // 4. Navigate to dashboard upon successful completion
        if (typeof window !== "undefined") {
          window.location.replace("/");
        } else {
          router.replace("/");
        }
      } catch (err: unknown) {
        const rawMsg = err instanceof Error ? err.message : String(err);
        let clearMsg = "Login aborted: Authentication failed.";

        const lower = rawMsg.toLowerCase();
        if (lower.includes("state") || lower.includes("no matching state")) {
          clearMsg = "Login aborted: Invalid or mismatched authentication state.";
        } else if (lower.includes("nonce")) {
          clearMsg = "Login aborted: Cryptographic nonce mismatch in ID token.";
        } else if (lower.includes("verifier") || lower.includes("code_verifier")) {
          clearMsg = "Login aborted: Missing or invalid PKCE code verifier.";
        } else {
          clearMsg = `Login aborted: ${rawMsg}`;
        }

        const encodedErr = encodeURIComponent("auth_callback_failed");
        const encodedDesc = encodeURIComponent(clearMsg);
        const targetUrl = `/login?error=${encodedErr}&error_description=${encodedDesc}`;
        if (typeof window !== "undefined") {
          window.location.replace(targetUrl);
        } else {
          router.replace(targetUrl);
        }
      }
    }

    processCallback();
  }, [router, searchParams, refresh]);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-[#0F121C] text-white p-4">
      <div className="flex flex-col items-center max-w-sm w-full p-8 rounded-xl border border-[#262A35] bg-[#171B26] shadow-2xl text-center">
        <div className="relative mb-4 flex h-14 w-14 items-center justify-center">
          <Image
            src="/logo.png"
            alt="MANTA Logo"
            width={56}
            height={56}
            priority
            className="w-14 h-14 object-contain drop-shadow-[0px_4px_12px_rgba(147,51,234,0.35)]"
          />
        </div>
        <div className="mb-4 h-6 w-6 animate-spin rounded-full border-2 border-purple-500 border-t-transparent" />
        <h2 className="text-lg font-semibold tracking-wide text-white">Completing Sign-In</h2>
        <p className="mt-2 text-xs text-[#94A3B8]">{statusMessage}</p>
      </div>
    </main>
  );
}
