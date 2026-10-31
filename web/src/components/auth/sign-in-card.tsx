"use client";

import React, { useState } from "react";
import Image from "next/image";
import { Loader2 } from "lucide-react";
import { initiateLogin } from "@/lib/auth";
import { SsoShieldIcon, AlertErrorIcon } from "@/components/icons/auth-icons";

interface SignInCardProps {
  initialError?: string | null;
}

export function SignInCard({ initialError = null }: SignInCardProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isLoadingSso, setIsLoadingSso] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(initialError);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const validateEmail = (val: string): string | null => {
    const trimmed = val.trim();
    if (!trimmed) {
      return "Please enter your work email";
    }
    const emailRegex = /^[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}$/;
    if (!emailRegex.test(trimmed)) {
      return "Invalid email address";
    }
    return null;
  };

  const validatePassword = (val: string): string | null => {
    if (!val || val.trim() === "" || val === "••••••••••••") {
      return "Incorrect password";
    }
    return null;
  };

  const handleCompanySso = async () => {
    try {
      setIsLoadingSso(true);
      setErrorMessage(null);
      setEmailError(null);
      setPasswordError(null);
      await initiateLogin();
    } catch (err: unknown) {
      setIsLoadingSso(false);
      const msg = err instanceof Error ? err.message : "Failed to initiate SSO authentication";
      setErrorMessage(msg);
    }
  };

  const handleCredentialsSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const emailErr = validateEmail(email);
    const passErr = validatePassword(password);

    setEmailError(emailErr);
    setPasswordError(passErr);

    if (emailErr || passErr) {
      setErrorMessage(
        "Authentication failed\nInvalid credentials or SSO sign-in was cancelled. Verify your email and password."
      );
    } else {
      setErrorMessage(null);
    }
  };

  return (
    <section
      aria-labelledby="signin-heading"
      className="relative flex flex-col items-center w-full max-w-[380px] rounded-[12px] border border-[#262A35] bg-[#171B26] p-4 sm:p-8 shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.4)] isolate text-white select-none transition-all"
      style={{
        fontFamily: "'Plus Jakarta Sans', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      }}
    >
      {/* Header */}
      <header className="flex flex-col items-center w-full mb-4 sm:mb-5">
        {/* M.A.N.T.A. Logo */}
        <div className="relative mb-2.5 sm:mb-3 flex h-[48px] w-[48px] sm:h-[56px] sm:w-[56px] items-center justify-center">
          <Image
            src="/logo.png"
            alt="M.A.N.T.A. Platform Logo"
            width={56}
            height={56}
            priority
            className="w-[48px] h-[48px] sm:w-[56px] sm:h-[56px] object-contain drop-shadow-[0px_4px_12px_rgba(147,51,234,0.25)]"
          />
        </div>

        {/* Heading */}
        <h1
          id="signin-heading"
          className="text-[22px] sm:text-[24px] font-bold tracking-[1px] text-white uppercase text-center leading-7 sm:leading-8"
        >
          M.A.N.T.A.
        </h1>
        <p className="text-[11px] sm:text-[12px] font-normal text-[#94A3B8] text-center mt-1 leading-[16px] sm:leading-[18px]">
          Engineering Intelligence Platform
        </p>
      </header>

      {/* Inline Error Alert (Figma Node 475:420 / 475:437) */}
      {errorMessage && (
        <div
          role="alert"
          aria-live="assertive"
          data-testid="auth-error-alert"
          className="mb-3.5 sm:mb-4 flex w-full flex-row items-start gap-[8px] sm:gap-[10px] rounded-[4px] border border-[rgba(255,77,77,0.45)] bg-[rgba(255,77,77,0.09)] p-[10px_10px] sm:p-[11px_12px]"
          style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}
        >
          {/* Alert Error SVG Icon from Figma */}
          <div className="shrink-0 mt-[1px]" aria-hidden="true">
            <AlertErrorIcon className="w-[18px] h-[18px]" />
          </div>
          <div className="flex flex-col gap-[2px] text-left">
            <span className="text-[12px] font-semibold text-[#FF8080] leading-[16px]">
              Authentication failed
            </span>
            <span className="text-[11px] font-normal text-[#EBBEBE] leading-[15px] sm:leading-[15.5px]">
              {errorMessage.includes("\n")
                ? errorMessage.split("\n")[1]
                : errorMessage || "Invalid credentials or SSO sign-in was cancelled. Verify your email and password."}
            </span>
          </div>
        </div>
      )}

      {/* SSO Actions - Only Continue with Company SSO (Google SSO removed in Figma 475:726) */}
      <div className="flex flex-col w-full gap-[10px] mb-4 sm:mb-5">
        <button
          type="button"
          data-testid="company-sso-button"
          onClick={handleCompanySso}
          disabled={isLoadingSso}
          aria-busy={isLoadingSso}
          className="relative flex h-[44px] min-h-[44px] w-full items-center justify-center rounded-[12px] bg-[#00E599] font-semibold text-[13px] sm:text-[14px] leading-5 text-black shadow-[0px_2px_10px_rgba(0,245,160,0.2)] transition-all hover:bg-[#00f5a0] active:scale-[0.99] disabled:opacity-75 disabled:cursor-not-allowed pl-9 pr-3 sm:px-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00E599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#171B26]"
        >
          <div className="absolute left-3 sm:left-4 flex items-center justify-center pointer-events-none">
            {isLoadingSso ? (
              <Loader2 className="h-4 w-4 animate-spin text-[#006B43]" aria-hidden="true" />
            ) : (
              <SsoShieldIcon className="w-4 h-4 shrink-0" aria-hidden="true" />
            )}
          </div>
          <span className="text-center select-none whitespace-nowrap">
            {isLoadingSso ? "Connecting to SSO..." : "Continue with Company SSO"}
          </span>
        </button>
      </div>

      {/* Divider */}
      <div
        className="relative flex w-full items-center justify-center h-[17px] mb-4 sm:mb-5"
        role="separator"
        aria-label="Sign in credentials separator"
      >
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-[#262A35]" />
        </div>
        <div className="relative bg-[#171B26] px-2.5">
          <span
            className="text-[10px] sm:text-[11px] font-medium tracking-[0.55px] text-[#B9CBBD]/80 uppercase leading-4"
            style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}
          >
            OR SIGN IN WITH CREDENTIALS
          </span>
        </div>
      </div>

      {/* Credentials Form */}
      <form
        onSubmit={handleCredentialsSubmit}
        className="flex flex-col w-full gap-[14px]"
        aria-label="Sign in with email and password"
        noValidate
      >
        {/* Work Email Field */}
        <div className="flex flex-col gap-1.5 w-full">
          <div
            className={`flex flex-col justify-between rounded-[4px] border bg-[#0A0E18] px-[12px] sm:px-[14px] py-[8px] h-[56px] box-border transition-colors ${
              emailError
                ? "border-[#FF4D4D] focus-within:ring-1 focus-within:ring-[#FF4D4D]"
                : "border-[#262A35] focus-within:border-[#00C2CB] focus-within:ring-1 focus-within:ring-[#00C2CB]"
            }`}
          >
            <label htmlFor="work-email" className="flex items-center gap-[3px] h-[12px] leading-3 cursor-pointer">
              <span className="text-[10px] font-medium text-[#94A3B8]">Work email</span>
              <span className="text-[12px] font-bold text-[#FF4D4D] leading-none" aria-hidden="true">*</span>
              <span className="sr-only">(required)</span>
            </label>
            <input
              id="work-email"
              name="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                if (emailError) setEmailError(null);
              }}
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-medium text-[#DFE2F1] placeholder-[#94A3B8]/50 [color-scheme:dark]"
              placeholder="alex@manta.ai"
              required
              aria-required="true"
              aria-invalid={!!emailError}
              aria-describedby={emailError ? "email-error-message" : undefined}
            />
          </div>
          {emailError && (
            <span
              id="email-error-message"
              role="alert"
              aria-live="polite"
              className="text-[12px] font-medium text-[#FF7878] leading-4"
            >
              {emailError}
            </span>
          )}
        </div>

        {/* Password Field */}
        <div className="flex flex-col gap-1.5 w-full">
          <div
            className={`flex flex-col justify-between rounded-[4px] border bg-[#0A0E18] px-[14px] py-[8px] h-[56px] box-border transition-colors ${
              passwordError
                ? "border-[#FF4D4D] focus-within:ring-1 focus-within:ring-[#FF4D4D]"
                : "border-[#262A35] focus-within:border-[#00C2CB] focus-within:ring-1 focus-within:ring-[#00C2CB]"
            }`}
          >
            <div className="flex items-center justify-between h-[14px] leading-none">
              <label htmlFor="password" className="flex items-center gap-[3px] cursor-pointer">
                <span className="text-[10px] font-medium text-[#94A3B8]">Password</span>
                <span className="text-[12px] font-bold text-[#FF4D4D] leading-none" aria-hidden="true">*</span>
                <span className="sr-only">(required)</span>
              </label>
              <a
                href="#forgot"
                className="text-[11px] font-normal text-[#00C2CB] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00C2CB] focus-visible:rounded-[2px] py-1 px-1 -my-1 -mr-1"
                style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}
              >
                Forgot password?
              </a>
            </div>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                if (passwordError) setPasswordError(null);
              }}
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-normal tracking-[1.5px] text-[#DFE2F1] placeholder-[#94A3B8]/50 placeholder:font-normal placeholder:tracking-[2px] [color-scheme:dark]"
              style={{ fontFamily: "'Plus Jakarta Sans', sans-serif" }}
              placeholder="••••••••••••"
              required
              aria-required="true"
              aria-invalid={!!passwordError}
              aria-describedby={passwordError ? "password-error-message" : undefined}
            />
          </div>
          {passwordError && (
            <span
              id="password-error-message"
              role="alert"
              aria-live="polite"
              className="text-[12px] font-medium text-[#FF7878] leading-4"
            >
              {passwordError}
            </span>
          )}
        </div>

        {/* Credentials Submit Button */}
        <button
          type="submit"
          className="flex h-[44px] min-h-[44px] w-full items-center justify-center gap-1.5 rounded-[12px] bg-[#00E599] font-semibold text-[14px] leading-5 text-black transition-all hover:bg-[#00f5a0] active:scale-[0.99] mt-0 shadow-[0px_2px_10px_rgba(0,245,160,0.15)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00E599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#171B26]"
        >
          <span>Sign In</span>
          <svg
            className="w-3.5 h-3.5"
            viewBox="0 0 14 14"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            aria-hidden="true"
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
          <a
            href="#signup"
            className="font-semibold text-[#00C2CB] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00C2CB] focus-visible:rounded-[2px] py-1 px-1 -my-1 -mx-1"
          >
            Sign up
          </a>
        </div>
      </form>
    </section>
  );
}
