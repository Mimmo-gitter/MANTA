import { Suspense } from "react";
import type { Metadata } from "next";
import { LoginContent } from "./login-content";

export const metadata: Metadata = {
  title: "Sign In | M.A.N.T.A.",
  description: "Sign in to the M.A.N.T.A. Engineering Intelligence Platform",
};

export default function LoginPage() {
  return (
    <div className="relative min-h-screen w-full flex flex-col justify-center items-center overflow-hidden bg-[#0F131D] px-4 py-8">
      {/* Ambient background glow matching Figma design */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 -top-24 -translate-x-1/2 w-[500px] h-[500px] rounded-full bg-[rgba(147,51,234,0.06)] blur-[80px]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute left-1/2 -bottom-20 -translate-x-1/2 w-[400px] h-[400px] rounded-full bg-[rgba(0,245,160,0.05)] blur-[70px]"
      />

      <div className="relative z-10 w-full flex justify-center">
        <Suspense
          fallback={
            <div className="w-[380px] h-[578px] rounded-[12px] border border-[#262A35] bg-[#171B26] animate-pulse" />
          }
        >
          <LoginContent />
        </Suspense>
      </div>
    </div>
  );
}
