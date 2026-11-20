import { Suspense } from "react";
import type { Metadata } from "next";
import { LoginContent } from "./login-content";

export const metadata: Metadata = {
  title: "Sign In | M.A.N.T.A.",
  description: "Sign in to the M.A.N.T.A. Engineering Intelligence Platform",
};

export default function LoginPage() {
  return (
    <main
      id="main-content"
      className="relative min-h-screen w-full flex flex-col justify-center items-center overflow-x-hidden bg-[#0F131D] px-2.5 py-4 sm:px-4 sm:py-8"
    >
      {/* Ambient background glow matching Figma design, scaled gracefully up to 4K */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 -top-24 -translate-x-1/2 w-[500px] h-[500px] sm:w-[650px] sm:h-[650px] 2xl:w-[1000px] 2xl:h-[1000px] rounded-full bg-[rgba(147,51,234,0.06)] blur-[80px] sm:blur-[110px] 2xl:blur-[160px]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 -bottom-20 -translate-x-1/2 w-[400px] h-[400px] sm:w-[500px] sm:h-[500px] 2xl:w-[800px] 2xl:h-[800px] rounded-full bg-[rgba(0,245,160,0.05)] blur-[70px] sm:blur-[95px] 2xl:blur-[140px]"
      />

      <div className="relative z-10 w-full flex justify-center">
        <Suspense
          fallback={
            <div
              className="w-full max-w-[380px] h-[524px] rounded-[12px] border border-[#262A35] bg-[#171B26] animate-pulse"
              role="status"
              aria-label="Loading sign in card"
            />
          }
        >
          <LoginContent />
        </Suspense>
      </div>
    </main>
  );
}
