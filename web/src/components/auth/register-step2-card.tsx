"use client";

import React, { useState } from "react";
import Image from "next/image";
import { ArrowRightMintIcon, AlertErrorIcon } from "@/components/icons/auth-icons";

export interface Step2Data {
  adminName: string;
  adminEmail: string;
  password: string;
  confirmPassword: string;
}

interface RegisterStep2CardProps {
  data: Step2Data;
  organizationName: string;
  onChange: (updated: Partial<Step2Data>) => void;
  onNext: () => void;
  onBack: () => void;
}

export function RegisterStep2Card({
  data,
  organizationName,
  onChange,
  onNext,
  onBack,
}: RegisterStep2CardProps) {
  const [nameTouched, setNameTouched] = useState(false);
  const [emailTouched, setEmailTouched] = useState(false);
  const [passwordTouched, setPasswordTouched] = useState(false);
  const [confirmTouched, setConfirmTouched] = useState(false);

  // Password rules validation
  const hasMinLength = data.password.length >= 12;
  const hasUppercase = /[A-Z]/.test(data.password);
  const hasNumber = /[0-9]/.test(data.password);
  const hasSymbol = /[^A-Za-z0-9]/.test(data.password);
  const isPasswordValid = hasMinLength && hasUppercase && hasNumber && hasSymbol;
  const doPasswordsMatch = data.password === data.confirmPassword && data.password.length > 0;

  // Personal/email validation
  const isNameValid = data.adminName.trim().length >= 2;
  const emailRegex = /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$/;
  const isEmailValid = emailRegex.test(data.adminEmail.trim());

  const canContinue = isNameValid && isEmailValid && isPasswordValid && doPasswordsMatch;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setNameTouched(true);
    setEmailTouched(true);
    setPasswordTouched(true);
    setConfirmTouched(true);
    if (canContinue) {
      onNext();
    }
  };

  const showValidationPill =
    (passwordTouched && !isPasswordValid) ||
    (emailTouched && !isEmailValid) ||
    (confirmTouched && !doPasswordsMatch);

  return (
    <section
      aria-labelledby="register-step2-heading"
      className="relative flex flex-col items-center w-full max-w-[420px] rounded-[12px] border border-[#262A35] bg-[#171B26] p-6 sm:p-8 shadow-[0px_8px_32px_-4px_rgba(0,0,0,0.5),0px_2px_8px_-2px_rgba(0,0,0,0.3)] isolate text-white select-none transition-all"
      style={{
        fontFamily: "'Plus Jakarta Sans', system-ui, -apple-system, sans-serif",
      }}
    >
      {/* Header */}
      <header className="flex flex-col items-center w-full mb-4">
        {/* M.A.N.T.A. Logo */}
        <div className="relative mb-3 flex h-[48px] w-[48px] items-center justify-center">
          <Image
            src="/logo.png"
            alt="M.A.N.T.A. Platform Logo"
            width={48}
            height={48}
            priority
            className="w-[48px] h-[48px] object-contain drop-shadow-[0px_4px_12px_rgba(147,51,234,0.25)]"
          />
        </div>

        {/* Status Pill (Figma 491:1057 vs 491:1238) */}
        {showValidationPill ? (
          <div
            data-testid="step2-status-pill-error"
            className="mb-2 bg-[#0A0E18] border border-[rgba(255,77,77,0.4)] rounded-[4px] px-[8px] py-[2px] flex items-center gap-[6px]"
          >
            <div className="bg-[#FF4D4D] rounded-full size-[6px] shrink-0" aria-hidden="true" />
            <span className="font-mono font-medium text-[11px] leading-[16.5px] tracking-[0.55px] text-[#FF4D4D] uppercase">
              STEP 2 · VALIDATION ERROR
            </span>
          </div>
        ) : (
          <div
            data-testid="step2-status-pill"
            className="mb-2 bg-[#0A0E18] border border-[rgba(0,229,153,0.4)] rounded-[4px] px-[8px] py-[2px] flex items-center gap-[6px]"
          >
            <div className="bg-[#00E599] rounded-full size-[6px] shrink-0" aria-hidden="true" />
            <span className="font-mono font-medium text-[11px] leading-[16.5px] tracking-[0.55px] text-[#00E599] uppercase">
              STEP 2 OF 3 · ADMIN CREDENTIALS
            </span>
          </div>
        )}

        <h1
          id="register-step2-heading"
          className="text-[22px] sm:text-[24px] font-bold tracking-[-0.2px] text-[#DFE2F1] text-center leading-[30px] sm:leading-[32px]"
        >
          Create Administrator Account
        </h1>
        <p className="text-[12px] text-[#94A3B8] text-center mt-1 leading-[18px]">
          This account receives root Tenant Administrator privileges for{" "}
          <span className="text-[#DFE2F1] font-semibold">{organizationName || "your workspace"}</span>.
        </p>
      </header>

      {/* Step Indicator (Figma 491:1062) */}
      <div
        className="flex gap-[8px] items-center justify-center w-full mb-4"
        role="progressbar"
        aria-label="Registration Progress"
        aria-valuenow={2}
        aria-valuemin={1}
        aria-valuemax={3}
      >
        <div className="bg-[#00E599] h-[4px] rounded-[2px] w-[40px] transition-colors" />
        <div className="bg-[#00E599] h-[4px] rounded-[2px] w-[40px] transition-colors" />
        <div className="bg-[rgba(148,163,184,0.25)] h-[4px] rounded-[2px] w-[40px] transition-colors" />
      </div>

      {/* Validation Error Banner (Figma 491:1243) */}
      {showValidationPill && (
        <div
          role="alert"
          aria-live="polite"
          data-testid="step2-validation-alert"
          className="mb-3.5 flex w-full flex-row items-start gap-[10px] rounded-[4px] border border-[rgba(255,77,77,0.45)] bg-[rgba(255,77,77,0.09)] p-[10px]"
        >
          <div className="shrink-0 mt-[1px]" aria-hidden="true">
            <AlertErrorIcon className="w-[16px] h-[16px]" />
          </div>
          <div className="flex flex-col gap-[2px] text-left">
            <span className="text-[12px] font-semibold text-[#FF8080] leading-[15px]">
              Form validation error
            </span>
            <span className="text-[11px] font-normal text-[#EBBEBE] leading-[15px]">
              Corporate business email required and password must satisfy complexity checklist.
            </span>
          </div>
        </div>
      )}

      {/* Step 2 Form */}
      <form
        onSubmit={handleSubmit}
        className="flex flex-col w-full gap-[12px]"
        aria-label="Step 2: Administrator Credentials Form"
        noValidate
      >
        {/* Administrator Full Name */}
        <div className="flex flex-col gap-1 w-full">
          <div
            className={`flex flex-col justify-between rounded-[4px] border bg-[#0A0E18] px-[14px] py-[8px] h-[56px] box-border transition-colors ${
              nameTouched && !isNameValid
                ? "border-[#FF4D4D] focus-within:ring-1 focus-within:ring-[#FF4D4D]"
                : "border-[#262A35] focus-within:border-[#00C2CB] focus-within:ring-1 focus-within:ring-[#00C2CB]"
            }`}
          >
            <label
              htmlFor="admin-name"
              className="flex items-center gap-[3px] h-[12px] leading-3 cursor-pointer"
            >
              <span className="text-[10px] font-medium text-[#94A3B8]">Administrator full name</span>
              <span className="text-[12px] font-bold text-[#FF4D4D] leading-none" aria-hidden="true">*</span>
              <span className="sr-only">(required)</span>
            </label>
            <input
              id="admin-name"
              name="adminName"
              type="text"
              autoComplete="name"
              value={data.adminName}
              onChange={(e) => onChange({ adminName: e.target.value })}
              onBlur={() => setNameTouched(true)}
              placeholder="Pavlo Zhuk"
              required
              aria-required="true"
              aria-invalid={nameTouched && !isNameValid}
              aria-describedby={nameTouched && !isNameValid ? "admin-name-error" : undefined}
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-medium text-[#DFE2F1] placeholder-[#94A3B8]/40 [color-scheme:dark]"
            />
          </div>
          {nameTouched && !isNameValid && (
            <span id="admin-name-error" role="alert" className="text-[11px] font-medium text-[#FF7878] leading-4">
              Administrator name must be at least 2 characters.
            </span>
          )}
        </div>

        {/* Corporate Work Email */}
        <div className="flex flex-col gap-1 w-full">
          <div
            className={`flex flex-col justify-between rounded-[4px] border bg-[#0A0E18] px-[14px] py-[8px] h-[56px] box-border transition-colors ${
              emailTouched && !isEmailValid
                ? "border-[#FF4D4D] focus-within:ring-1 focus-within:ring-[#FF4D4D]"
                : "border-[#262A35] focus-within:border-[#00C2CB] focus-within:ring-1 focus-within:ring-[#00C2CB]"
            }`}
          >
            <label
              htmlFor="admin-email"
              className="flex items-center gap-[3px] h-[12px] leading-3 cursor-pointer"
            >
              <span className="text-[10px] font-medium text-[#94A3B8]">Corporate work email</span>
              <span className="text-[12px] font-bold text-[#FF4D4D] leading-none" aria-hidden="true">*</span>
              <span className="sr-only">(required)</span>
            </label>
            <input
              id="admin-email"
              name="adminEmail"
              type="email"
              autoComplete="email"
              value={data.adminEmail}
              onChange={(e) => onChange({ adminEmail: e.target.value })}
              onBlur={() => setEmailTouched(true)}
              placeholder="admin@fintech.com"
              required
              aria-required="true"
              aria-invalid={emailTouched && !isEmailValid}
              aria-describedby={emailTouched && !isEmailValid ? "admin-email-error" : undefined}
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-medium text-[#DFE2F1] placeholder-[#94A3B8]/40 [color-scheme:dark]"
            />
          </div>
          {emailTouched && !isEmailValid && (
            <span id="admin-email-error" role="alert" className="text-[11px] font-medium text-[#FF7878] leading-4">
              Please enter a valid corporate email address.
            </span>
          )}
        </div>

        {/* Create Password */}
        <div className="flex flex-col gap-1 w-full">
          <div
            className={`flex flex-col justify-between rounded-[4px] border bg-[#0A0E18] px-[14px] py-[8px] h-[56px] box-border transition-colors ${
              passwordTouched && !isPasswordValid
                ? "border-[#FF4D4D] focus-within:ring-1 focus-within:ring-[#FF4D4D]"
                : "border-[#262A35] focus-within:border-[#00C2CB] focus-within:ring-1 focus-within:ring-[#00C2CB]"
            }`}
          >
            <label
              htmlFor="create-password"
              className="flex items-center gap-[3px] h-[12px] leading-3 cursor-pointer"
            >
              <span className="text-[10px] font-medium text-[#94A3B8]">Create password</span>
              <span className="text-[12px] font-bold text-[#FF4D4D] leading-none" aria-hidden="true">*</span>
              <span className="sr-only">(required)</span>
            </label>
            <input
              id="create-password"
              name="password"
              type="password"
              autoComplete="new-password"
              value={data.password}
              onChange={(e) => onChange({ password: e.target.value })}
              onBlur={() => setPasswordTouched(true)}
              placeholder="••••••••••••"
              required
              aria-required="true"
              aria-invalid={passwordTouched && !isPasswordValid}
              aria-describedby="password-rules-checklist"
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-medium text-[#DFE2F1] placeholder-[#94A3B8]/40 tracking-[2px] [color-scheme:dark]"
            />
          </div>
        </div>

        {/* Confirm Password */}
        <div className="flex flex-col gap-1 w-full">
          <div
            className={`flex flex-col justify-between rounded-[4px] border bg-[#0A0E18] px-[14px] py-[8px] h-[56px] box-border transition-colors ${
              confirmTouched && !doPasswordsMatch
                ? "border-[#FF4D4D] focus-within:ring-1 focus-within:ring-[#FF4D4D]"
                : "border-[#262A35] focus-within:border-[#00C2CB] focus-within:ring-1 focus-within:ring-[#00C2CB]"
            }`}
          >
            <label
              htmlFor="confirm-password"
              className="flex items-center gap-[3px] h-[12px] leading-3 cursor-pointer"
            >
              <span className="text-[10px] font-medium text-[#94A3B8]">Confirm password</span>
              <span className="text-[12px] font-bold text-[#FF4D4D] leading-none" aria-hidden="true">*</span>
              <span className="sr-only">(required)</span>
            </label>
            <input
              id="confirm-password"
              name="confirmPassword"
              type="password"
              autoComplete="new-password"
              value={data.confirmPassword}
              onChange={(e) => onChange({ confirmPassword: e.target.value })}
              onBlur={() => setConfirmTouched(true)}
              placeholder="••••••••••••"
              required
              aria-required="true"
              aria-invalid={confirmTouched && !doPasswordsMatch}
              aria-describedby={confirmTouched && !doPasswordsMatch ? "confirm-password-error" : undefined}
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-medium text-[#DFE2F1] placeholder-[#94A3B8]/40 tracking-[2px] [color-scheme:dark]"
            />
          </div>
          {confirmTouched && !doPasswordsMatch && (
            <span id="confirm-password-error" role="alert" className="text-[11px] font-medium text-[#FF7878] leading-4">
              Passwords do not match.
            </span>
          )}
        </div>

        {/* Live Password Complexity Checklist (Figma 491:1095 & 491:1273) */}
        <div
          id="password-rules-checklist"
          data-testid="password-rules-checklist"
          className="flex flex-wrap items-center justify-between gap-y-1 text-[11px] px-1 py-1 font-mono select-none"
        >
          <span
            data-testid="rule-min-length"
            className={`flex items-center gap-1 ${
              hasMinLength
                ? "text-[#00E599]"
                : passwordTouched
                ? "text-[#FF4D4D]"
                : "text-[#94A3B8]/70"
            }`}
          >
            <span aria-hidden="true">{hasMinLength ? "✓" : "✗"}</span>
            <span>Min 12 chars</span>
          </span>

          <span
            data-testid="rule-uppercase"
            className={`flex items-center gap-1 ${
              hasUppercase
                ? "text-[#00E599]"
                : passwordTouched
                ? "text-[#FF4D4D]"
                : "text-[#94A3B8]/70"
            }`}
          >
            <span aria-hidden="true">{hasUppercase ? "✓" : "✗"}</span>
            <span>1 uppercase</span>
          </span>

          <span
            data-testid="rule-number"
            className={`flex items-center gap-1 ${
              hasNumber
                ? "text-[#00E599]"
                : passwordTouched
                ? "text-[#FF4D4D]"
                : "text-[#94A3B8]/70"
            }`}
          >
            <span aria-hidden="true">{hasNumber ? "✓" : "✗"}</span>
            <span>1 number</span>
          </span>

          <span
            data-testid="rule-symbol"
            className={`flex items-center gap-1 ${
              hasSymbol
                ? "text-[#00E599]"
                : passwordTouched
                ? "text-[#FF4D4D]"
                : "text-[#94A3B8]/70"
            }`}
          >
            <span aria-hidden="true">{hasSymbol ? "✓" : "✗"}</span>
            <span>1 symbol</span>
          </span>
        </div>

        {/* Action Buttons (Figma 491:1100) */}
        <div className="flex flex-col gap-2 mt-2 w-full">
          <button
            type="submit"
            data-testid="step2-continue-button"
            disabled={!canContinue}
            className="flex h-[44px] min-h-[44px] w-full items-center justify-center gap-2 rounded-[12px] bg-[#00E599] font-semibold text-[14px] leading-5 text-black transition-all hover:bg-[#00f5a0] active:scale-[0.99] shadow-[0px_2px_10px_rgba(0,245,160,0.15)] disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00E599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#171B26]"
          >
            <span>Continue to Team Invitation</span>
            <ArrowRightMintIcon className="w-4 h-4 shrink-0" />
          </button>

          <button
            type="button"
            data-testid="step2-back-button"
            onClick={onBack}
            className="flex h-[42px] w-full items-center justify-center rounded-[8px] border border-[#262A35] bg-[#0A0E18] text-[13px] font-medium text-[#DFE2F1] hover:bg-[#161B26] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00E599]"
          >
            ← Back to Organization Details
          </button>
        </div>
      </form>
    </section>
  );
}
