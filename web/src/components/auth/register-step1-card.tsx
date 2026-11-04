"use client";

import React from "react";
import Image from "next/image";
import Link from "next/link";
import { AlertErrorIcon, ArrowRightMintIcon } from "@/components/icons/auth-icons";

export interface Step1Data {
  organizationName: string;
  tenantSlug: string;
  industry: string;
}

interface RegisterStep1CardProps {
  data: Step1Data;
  onChange: (updated: Partial<Step1Data>) => void;
  onNext: () => void;
  duplicateError?: string | null;
  onClearDuplicateError?: () => void;
}

export function RegisterStep1Card({
  data,
  onChange,
  onNext,
  duplicateError,
  onClearDuplicateError,
}: RegisterStep1CardProps) {
  const [nameTouched, setNameTouched] = React.useState(false);
  const [slugTouched, setSlugTouched] = React.useState(false);

  // Auto-generate slug from organization name if slug hasn't been manually edited
  const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newName = e.target.value;
    if (duplicateError && onClearDuplicateError) {
      onClearDuplicateError();
    }
    if (!slugTouched) {
      const derivedSlug = newName
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, "")
        .trim()
        .replace(/[\s-]+/g, "-");
      onChange({ organizationName: newName, tenantSlug: derivedSlug });
    } else {
      onChange({ organizationName: newName });
    }
  };

  const handleSlugChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSlugTouched(true);
    if (duplicateError && onClearDuplicateError) {
      onClearDuplicateError();
    }
    onChange({ tenantSlug: e.target.value.toLowerCase() });
  };

  const isNameValid = data.organizationName.trim().length >= 2;
  const isSlugValid = /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(data.tenantSlug.trim());
  const canContinue = isNameValid && isSlugValid;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setNameTouched(true);
    setSlugTouched(true);
    if (canContinue) {
      onNext();
    }
  };

  return (
    <section
      aria-labelledby="register-step1-heading"
      className="relative flex flex-col items-center w-full max-w-[420px] rounded-[12px] border border-[#262A35] bg-[#171B26] p-6 sm:p-8 shadow-[0px_8px_32px_-4px_rgba(0,0,0,0.5),0px_2px_8px_-2px_rgba(0,0,0,0.3)] isolate text-white select-none transition-all"
      style={{
        fontFamily: "'Plus Jakarta Sans', system-ui, -apple-system, sans-serif",
      }}
    >
      {/* Header */}
      <header className="flex flex-col items-center w-full mb-5">
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

        {/* Status Pill (Figma 491:1011 vs 491:1196) */}
        {duplicateError ? (
          <div
            data-testid="step1-status-pill-duplicate"
            className="mb-2 bg-[#0A0E18] border border-[rgba(255,77,77,0.4)] rounded-[4px] px-[8px] py-[2px] flex items-center gap-[6px]"
          >
            <div className="bg-[#FF4D4D] rounded-full size-[6px] shrink-0" aria-hidden="true" />
            <span className="font-mono font-medium text-[11px] leading-[16.5px] tracking-[0.55px] text-[#FF4D4D] uppercase">
              STEP 1 · DUPLICATE DETECTED
            </span>
          </div>
        ) : (
          <div
            data-testid="step1-status-pill"
            className="mb-2 bg-[#0A0E18] border border-[rgba(0,229,153,0.4)] rounded-[4px] px-[8px] py-[2px] flex items-center gap-[6px]"
          >
            <div className="bg-[#00E599] rounded-full size-[6px] shrink-0" aria-hidden="true" />
            <span className="font-mono font-medium text-[11px] leading-[16.5px] tracking-[0.55px] text-[#00E599] uppercase">
              STEP 1 OF 3 · TENANT PROFILE
            </span>
          </div>
        )}

        {/* Heading */}
        <h1
          id="register-step1-heading"
          className="text-[24px] font-bold tracking-[-0.2px] text-[#DFE2F1] text-center leading-[32px]"
        >
          Register Your Organization
        </h1>
      </header>

      {/* Step Indicator (Figma 491:1015) */}
      <div
        className="flex gap-[8px] items-center justify-center w-full mb-5"
        role="progressbar"
        aria-label="Registration Progress"
        aria-valuenow={1}
        aria-valuemin={1}
        aria-valuemax={3}
      >
        <div className="bg-[#00E599] h-[4px] rounded-[2px] w-[40px] transition-colors" />
        <div className="bg-[rgba(148,163,184,0.25)] h-[4px] rounded-[2px] w-[40px] transition-colors" />
        <div className="bg-[rgba(148,163,184,0.25)] h-[4px] rounded-[2px] w-[40px] transition-colors" />
      </div>

      {/* Duplicate Error Alert (Figma 491:1200) */}
      {duplicateError && (
        <div
          role="alert"
          aria-live="assertive"
          data-testid="duplicate-slug-alert"
          className="mb-4 flex w-full flex-row items-start gap-[10px] rounded-[4px] border border-[rgba(255,77,77,0.45)] bg-[rgba(255,77,77,0.09)] p-[12px]"
        >
          <div className="shrink-0 mt-[1px]" aria-hidden="true">
            <AlertErrorIcon className="w-[18px] h-[18px]" />
          </div>
          <div className="flex flex-col gap-[2px] text-left">
            <span className="text-[12px] font-semibold text-[#FF8080] leading-[16px]">
              Organization already registered
            </span>
            <span className="text-[11px] font-normal text-[#EBBEBE] leading-[15.5px]">
              {duplicateError}
            </span>
          </div>
        </div>
      )}

      {/* Form Fields */}
      <form
        onSubmit={handleSubmit}
        className="flex flex-col w-full gap-[14px]"
        aria-label="Step 1: Organization Profile Form"
        noValidate
      >
        {/* Organization Name Field */}
        <div className="flex flex-col gap-1 w-full">
          <div
            className={`flex flex-col justify-between rounded-[4px] border bg-[#0A0E18] px-[14px] py-[8px] h-[56px] box-border transition-colors ${
              nameTouched && !isNameValid
                ? "border-[#FF4D4D] focus-within:ring-1 focus-within:ring-[#FF4D4D]"
                : "border-[#262A35] focus-within:border-[#00C2CB] focus-within:ring-1 focus-within:ring-[#00C2CB]"
            }`}
          >
            <label
              htmlFor="org-name"
              className="flex items-center gap-[3px] h-[12px] leading-3 cursor-pointer"
            >
              <span className="text-[10px] font-medium text-[#94A3B8]">Organization name</span>
              <span className="text-[12px] font-bold text-[#FF4D4D] leading-none" aria-hidden="true">*</span>
              <span className="sr-only">(required)</span>
            </label>
            <input
              id="org-name"
              name="organizationName"
              type="text"
              autoComplete="organization"
              value={data.organizationName}
              onChange={handleNameChange}
              onBlur={() => setNameTouched(true)}
              placeholder="Fintech Alpha LLC"
              required
              aria-required="true"
              aria-invalid={nameTouched && !isNameValid}
              aria-describedby={nameTouched && !isNameValid ? "org-name-error" : undefined}
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-medium text-[#DFE2F1] placeholder-[#94A3B8]/40 [color-scheme:dark]"
            />
          </div>
          {nameTouched && !isNameValid && (
            <span id="org-name-error" role="alert" className="text-[11px] font-medium text-[#FF7878] leading-4">
              Organization name must be at least 2 characters.
            </span>
          )}
        </div>

        {/* Tenant Slug Field */}
        <div className="flex flex-col gap-1 w-full">
          <div
            className={`flex flex-col justify-between rounded-[4px] border bg-[#0A0E18] px-[14px] py-[8px] h-[56px] box-border transition-colors ${
              (slugTouched && !isSlugValid) || duplicateError
                ? "border-[#FF4D4D] focus-within:ring-1 focus-within:ring-[#FF4D4D]"
                : "border-[#262A35] focus-within:border-[#00C2CB] focus-within:ring-1 focus-within:ring-[#00C2CB]"
            }`}
          >
            <div className="flex items-center justify-between h-[12px] leading-3">
              <label
                htmlFor="org-slug"
                className="flex items-center gap-[3px] cursor-pointer"
              >
                <span className="text-[10px] font-medium text-[#94A3B8]">Workspace URL / Tenant slug</span>
                <span className="text-[12px] font-bold text-[#FF4D4D] leading-none" aria-hidden="true">*</span>
                <span className="sr-only">(required)</span>
              </label>
              <span className="font-mono text-[11px] text-[#00C2CB] select-none" aria-hidden="true">
                manta.ai/app/
              </span>
            </div>
            <input
              id="org-slug"
              name="tenantSlug"
              type="text"
              autoComplete="off"
              value={data.tenantSlug}
              onChange={handleSlugChange}
              onBlur={() => setSlugTouched(true)}
              placeholder="fintech-alpha"
              required
              aria-required="true"
              aria-invalid={(slugTouched && !isSlugValid) || !!duplicateError}
              aria-describedby={
                slugTouched && !isSlugValid
                  ? "org-slug-error"
                  : duplicateError
                  ? "org-slug-duplicate-desc"
                  : undefined
              }
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-medium text-[#DFE2F1] placeholder-[#94A3B8]/40 [color-scheme:dark]"
            />
          </div>
          {slugTouched && !isSlugValid && (
            <span id="org-slug-error" role="alert" className="text-[11px] font-medium text-[#FF7878] leading-4">
              Slug must contain only lowercase letters, numbers, and hyphens (min 3 chars).
            </span>
          )}
          {duplicateError && (
            <span
              id="org-slug-duplicate-desc"
              className="text-[11px] font-normal text-[#FF8080] leading-[15px]"
            >
              This workspace identifier is already taken. Please choose a unique tenant slug.
            </span>
          )}
        </div>

        {/* Industry / Primary Domain Field */}
        <div className="flex flex-col gap-1 w-full">
          <div className="flex flex-col justify-between rounded-[4px] border border-[#262A35] bg-[#0A0E18] px-[14px] py-[8px] h-[56px] box-border transition-colors focus-within:border-[#00C2CB] focus-within:ring-1 focus-within:ring-[#00C2CB]">
            <label
              htmlFor="org-industry"
              className="flex items-center gap-[3px] h-[12px] leading-3 cursor-pointer"
            >
              <span className="text-[10px] font-medium text-[#94A3B8]">Industry / Primary domain</span>
              <span className="text-[10px] text-[#7C839B] font-mono ml-auto">Optional</span>
            </label>
            <input
              id="org-industry"
              name="industry"
              type="text"
              value={data.industry}
              onChange={(e) => onChange({ industry: e.target.value })}
              placeholder="Financial Services / FinTech"
              className="w-full bg-transparent p-0 m-0 border-0 outline-none ring-0 shadow-none focus:ring-0 focus:outline-none focus:border-0 text-[14px] leading-5 font-medium text-[#DFE2F1] placeholder-[#94A3B8]/40 [color-scheme:dark]"
            />
          </div>
        </div>

        {/* Primary CTA Button */}
        <button
          type="submit"
          data-testid="step1-continue-button"
          disabled={!canContinue}
          className="flex h-[44px] min-h-[44px] w-full items-center justify-center gap-2 rounded-[12px] bg-[#00E599] font-semibold text-[14px] leading-5 text-black transition-all hover:bg-[#00f5a0] active:scale-[0.99] mt-2 shadow-[0px_2px_10px_rgba(0,245,160,0.15)] disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00E599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#171B26]"
        >
          <span>Continue to Administrator Account</span>
          <ArrowRightMintIcon className="w-4 h-4 shrink-0" />
        </button>

        {/* Duplicate recovery actions (Figma 491:1223) */}
        {duplicateError && (
          <div className="flex flex-col gap-2 mt-1 w-full">
            <Link
              href={`/login?workspace=${encodeURIComponent(data.tenantSlug)}`}
              data-testid="sign-in-existing-account-button"
              className="flex h-[40px] w-full items-center justify-center rounded-[8px] border border-[#262A35] bg-[#0A0E18] text-[13px] font-medium text-[#DFE2F1] hover:bg-[#161B26] transition-colors"
            >
              Sign In with Existing Account
            </Link>
          </div>
        )}

        {/* Footer Link (Figma 491:1046) */}
        <div className="flex items-center justify-center gap-[5px] pt-3 text-[12px] leading-tight">
          <span className="text-[#94A3B8]">Already part of an organization?</span>
          <Link
            href="/login"
            data-testid="step1-login-link"
            className="font-semibold text-[#00C2CB] hover:text-[#00E599] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00C2CB] focus-visible:rounded-[4px] py-0.5 px-1 transition-colors"
          >
            Sign In
          </Link>
        </div>
      </form>
    </section>
  );
}
