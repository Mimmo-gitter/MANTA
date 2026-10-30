"use client";

import React, { useState } from "react";
import Image from "next/image";
import { Loader2 } from "lucide-react";
import { initiateLogin } from "@/lib/auth";
import { SsoShieldIcon } from "@/components/icons/auth-icons";

interface SignInCardProps {
  initialError?: string | null;
}

export function SignInCard({ initialError = null }: SignInCardProps) {
  const [email, setEmail] = useState("alex@manta.ai");
  const [password, setPassword] = useState("••••••••••••");
  const [isLoadingSso, setIsLoadingSso] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(initialError);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const handleCompanySso = async () => {
    try {
      setIsLoadingSso(true);
      setErrorMessage(null);
      await initiateLogin();
    } catch (err: unknown) {
      setIsLoadingSso(false);
      const msg = err instanceof Error ? err.message : "Failed to initiate SSO authentication";
      setErrorMessage(msg);
    }
  };

  const handleCredentialsSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!password || password === "••••••••••••") {
      setPasswordError("Incorrect password");
      setErrorMessage(
        "Authentication failed\nInvalid credentials or SSO sign-in was cancelled. Verify your email and password."
      );
    } else {
      setPasswordError(null);
    }
  };

  return (
    <div
      className="relative flex flex-col items-center w-[380px] max-w-[380px] rounded-[12px] border border-[#262A35] bg-[#171B26] p-8 shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.4)] isolate text-white select-none"
      style={{
        fontFamily: "'Plus Jakarta Sans', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      }}
    >
      {/* Header */}
      <div className="flex flex-col items-center w-full mb-5">
        {/* M.A.N.T.A. Logo */}
        <div className="relative mb-3 flex h-[56px] w-[56px] items-center justify-center">
          <Image
            src="/logo.png"
            alt="M.A.N.T.A. Logo"
            width={56}
            height={56}
            priority
            className="w-[56px] h-[56px] object-contain drop-shadow-[0px_4px_12px_rgba(147,51,234,0.35)]"
          />
        </div>

        {/* Heading */}
        <h1 className="text-[24px] font-bold tracking-[1px] text-white uppercase text-center leading-8">
          M.A.N.T.A.
        </h1>
        <p className="text-[12px] font-normal text-[#94A3B8] text-center mt-1 leading-[18px]">
          Engineering Intelligence Platform
        </p>
      </div>

      {/* Inline Error Alert (Figma File 5) */}
      {errorMessage && (
        <div
          role="alert"
          data-testid="auth-error-alert"
          className="mb-4 flex w-full flex-row items-start gap-2.5 rounded-[8px] border border-[rgba(255,77,77,0.28)] bg-[rgba(255,77,77,0.09)] p-[11px_12px]"
        >
          {/* Alert Circle Icon with ! */}
          <div className="flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-[rgba(255,77,77,0.22)] mt-0.5">
            <span className="text-[11px] font-bold text-[#FF8080] leading-none">!</span>
          </div>
          <div className="flex flex-col gap-0.5 text-left">
            <span className="text-[12px] font-semibold text-[#FF8080] leading-4">
              Authentication failed
            </span>
            <span className="text-[11px] text-[#DFE2F1]/85 leading-[15px]">
              {errorMessage.includes("\n")
                ? errorMessage.split("\n")[1]
                : errorMessage || "Invalid credentials or SSO sign-in was cancelled. Verify your email and password."}
            </span>
          </div>
        </div>
      )}

      {/* SSO Actions */}
      <div className="flex flex-col w-full gap-[10px] mb-5">
        {/* Continue with Company SSO Button */}
        <button
          type="button"
          data-testid="company-sso-button"
          onClick={handleCompanySso}
          disabled={isLoadingSso}
          className="flex h-[44px] w-full items-center justify-center gap-2 rounded-[12px] bg-[#00E599] font-semibold text-[14px] leading-5 text-black shadow-[0px_2px_10px_rgba(0,245,160,0.2)] transition-all hover:bg-[#00f5a0] active:scale-[0.99] disabled:opacity-75 disabled:cursor-not-allowed"
        >
          {isLoadingSso ? (
            <Loader2 className="h-4 w-4 animate-spin text-[#006B43]" />
          ) : (
            <SsoShieldIcon className="w-4 h-4 shrink-0" />
          )}
          <span>{isLoadingSso ? "Connecting to SSO..." : "Continue with Company SSO"}</span>
        </button>

        {/* Continue with Google Button */}
        <button
          type="button"
          data-testid="google-sso-button"
          className="flex h-[44px] w-full items-center justify-center gap-2 rounded-[12px] border border-[rgba(0,194,203,0.25)] bg-[#182028] font-semibold text-[14px] leading-5 text-[#00C2CB] transition-all hover:bg-[#1e2832] active:scale-[0.99]"
        >
          <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="#4285F4"
              d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
            />
            <path
              fill="#34A853"
              d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
            />
            <path
              fill="#FBBC05"
              d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
            />
            <path
              fill="#EA4335"
              d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
            />
          </svg>
          <span>Continue with Google</span>
        </button>
      </div>

      {/* Divider */}
      <div className="relative flex w-full items-center justify-center h-[17px] mb-5">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-[#262A35]" />
        </div>
        <div className="relative bg-[#171B26] px-2.5">
          <span
            className="text-[11px] font-medium tracking-[0.55px] text-[#B9CBBD]/80 uppercase leading-4"
            style={{ fontFamily: "'Inter', sans-serif" }}
          >
            OR SIGN IN WITH CREDENTIALS
          </span>
        </div>
      </div>

      {/* Credentials Form */}
      <form onSubmit={handleCredentialsSubmit} className="flex flex-col w-full gap-[14px]">
        {/* Work Email Field */}
        <div className="flex flex-col w-full">
          <div className="flex flex-col justify-between rounded-[4px] border border-[#262A35] bg-[#0A0E18] px-[14px] py-[8px] h-[56px] box-border transition-colors focus-within:border-[#00C2CB]">
            <div className="flex items-center gap-[3px] h-[12px] leading-3">
              <span className="text-[10px] font-medium text-[#94A3B8]">Work email</span>
              <span className="text-[12px] font-bold text-[#FF4D4D] leading-none">*</span>
            </div>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-medium text-[#DFE2F1] placeholder-[#94A3B8]/50"
              placeholder="alex@manta.ai"
              required
            />
          </div>
        </div>

        {/* Password Field */}
        <div className="flex flex-col gap-1.5 w-full">
          <div
            className={`flex flex-col justify-between rounded-[4px] border bg-[#0A0E18] px-[14px] py-[8px] h-[56px] box-border transition-colors ${
              passwordError
                ? "border-[#FF4D4D]"
                : "border-[#262A35] focus-within:border-[#00C2CB]"
            }`}
          >
            <div className="flex items-center justify-between h-[14px] leading-none">
              <div className="flex items-center gap-[3px]">
                <span className="text-[10px] font-medium text-[#94A3B8]">Password</span>
                <span className="text-[12px] font-bold text-[#FF4D4D] leading-none">*</span>
              </div>
              <a
                href="#forgot"
                className="text-[11px] font-normal text-[#00C2CB] hover:underline"
                style={{ fontFamily: "'JetBrains Mono', monospace" }}
              >
                Forgot password?
              </a>
            </div>
            <input
              type="password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                if (passwordError) setPasswordError(null);
              }}
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-normal tracking-[1.5px] text-[#DFE2F1]"
              style={{ fontFamily: "'JetBrains Mono', monospace" }}
              required
            />
          </div>
          {passwordError && (
            <span className="text-[12px] font-medium text-[#FF7878] leading-4">
              {passwordError}
            </span>
          )}
        </div>

        {/* Credentials Submit Button */}
        <button
          type="submit"
          className="flex h-[44px] w-full items-center justify-center gap-1.5 rounded-[12px] bg-[#00E599] font-semibold text-[14px] leading-5 text-black transition-all hover:bg-[#00f5a0] active:scale-[0.99] mt-0 shadow-[0px_2px_10px_rgba(0,245,160,0.15)]"
        >
          <span>Sign In</span>
          <svg
            className="w-3.5 h-3.5"
            viewBox="0 0 14 14"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              d="M2.5 7H11.5M11.5 7L7.5 3M11.5 7L7.5 11"
              stroke="#006B43"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>

        {/* Sign-Up Link Row */}
        <div className="flex items-center justify-center gap-[5px] h-[21px] pt-[4px] pb-[2px] text-[12px]">
          <span className="text-[#94A3B8]">Don&apos;t have an account?</span>
          <a href="#signup" className="font-semibold text-[#00C2CB] hover:underline">
            Sign up
          </a>
        </div>
      </form>
    </div>
  );
}
