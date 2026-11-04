"use client";

import React, { useState } from "react";
import Image from "next/image";
import { ArrowRightMintIcon, AlertErrorIcon, SecurityLockMintIcon } from "@/components/icons/auth-icons";

export interface Step3Data {
  invites: string[];
}

interface RegisterStep3CardProps {
  data: Step3Data;
  organizationName: string;
  onChange: (updated: Partial<Step3Data>) => void;
  onSubmit: () => void;
  onSkip: () => void;
  onBack: () => void;
  isSubmitting: boolean;
  error?: string | null;
}

export function RegisterStep3Card({
  data,
  organizationName,
  onChange,
  onSubmit,
  onSkip,
  onBack,
  isSubmitting,
  error,
}: RegisterStep3CardProps) {
  // Ensure we always have at least 2 invite rows initially for convenience
  const currentInvites = data.invites && data.invites.length > 0 ? data.invites : ["", ""];
  const [fieldErrors, setFieldErrors] = useState<Record<number, string>>({});

  const emailRegex = /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$/;

  const handleEmailChange = (index: number, value: string) => {
    const updated = [...currentInvites];
    updated[index] = value;
    onChange({ invites: updated });

    if (fieldErrors[index]) {
      const updatedErrors = { ...fieldErrors };
      delete updatedErrors[index];
      setFieldErrors(updatedErrors);
    }
  };

  const handleAddInvite = () => {
    onChange({ invites: [...currentInvites, ""] });
  };

  const handleRemoveInvite = (index: number) => {
    if (currentInvites.length <= 1) {
      onChange({ invites: [""] });
      return;
    }
    const updated = currentInvites.filter((_, i) => i !== index);
    onChange({ invites: updated });

    const updatedErrors = { ...fieldErrors };
    delete updatedErrors[index];
    setFieldErrors(updatedErrors);
  };

  const validateInvites = (): boolean => {
    const errors: Record<number, string> = {};
    currentInvites.forEach((inv, idx) => {
      const trimmed = inv.trim();
      if (trimmed.length > 0 && !emailRegex.test(trimmed)) {
        errors[idx] = "Please enter a valid work email address.";
      }
    });

    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isSubmitting) return;

    if (validateInvites()) {
      onSubmit();
    }
  };

  const handleSkipClick = () => {
    if (isSubmitting) return;
    onSkip();
  };

  return (
    <section
      aria-labelledby="register-step3-heading"
      className="relative flex flex-col items-center w-full max-w-[420px] rounded-[12px] border border-[#262A35] bg-[#171B26] p-6 sm:p-8 shadow-[0px_8px_32px_-4px_rgba(0,0,0,0.5),0px_2px_8px_-2px_rgba(0,0,0,0.3)] isolate text-white select-none transition-all"
      style={{
        fontFamily: "'Plus Jakarta Sans', system-ui, -apple-system, sans-serif",
      }}
    >
      {/* Header */}
      <header className="flex flex-col items-center w-full mb-5">
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

        {/* Status Pill (Figma 491:1107) */}
        <div
          data-testid="step3-status-pill"
          className="flex items-center gap-1.5 px-3 py-1 rounded-full border border-[#00E599]/30 bg-[#00E599]/10 text-[#00E599] text-[11px] font-semibold tracking-wider uppercase mb-3"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-[#00E599] animate-pulse" />
          <span>STEP 3 OF 3 · COLLABORATION</span>
        </div>

        <h1
          id="register-step3-heading"
          className="text-[22px] font-bold tracking-tight text-center text-[#DFE2F1]"
        >
          Invite Team Members
        </h1>

        <p className="text-[13px] text-[#94A3B8] text-center mt-1">
          Optional · Bring your team aboard to start collaborating in{" "}
          <span className="font-semibold text-white">{organizationName || "workspace"}</span>
        </p>
      </header>

      {/* Global / Server Error Message (if any) */}
      {error && (
        <div
          role="alert"
          aria-live="polite"
          data-testid="step3-error-banner"
          className="w-full flex items-start gap-2.5 p-3 rounded-[8px] border border-[#FF4D4D]/40 bg-[#FF4D4D]/10 text-[#FF6B6B] text-[13px] mb-4 leading-snug"
        >
          <AlertErrorIcon className="w-4 h-4 shrink-0 mt-0.5" />
          <div className="flex-1">
            <span className="font-semibold">Provisioning failed:</span> {error}
          </div>
        </div>
      )}

      {/* Form */}
      <form onSubmit={handleFormSubmit} noValidate className="flex flex-col gap-4 w-full">
        <div className="flex flex-col gap-3">
          {currentInvites.map((emailVal, index) => {
            const hasError = Boolean(fieldErrors[index]);
            return (
              <div key={index} className="flex flex-col gap-1 w-full">
                <div className="flex items-center justify-between">
                  <label
                    htmlFor={`invite-email-${index}`}
                    className="text-[12px] font-medium text-[#DFE2F1]"
                  >
                    Teammate {index + 1} email
                  </label>
                  {currentInvites.length > 1 && (
                    <button
                      type="button"
                      onClick={() => handleRemoveInvite(index)}
                      className="text-[11px] text-[#94A3B8] hover:text-[#FF6B6B] transition-colors focus-visible:outline-none"
                      aria-label={`Remove teammate ${index + 1}`}
                    >
                      Remove
                    </button>
                  )}
                </div>

                <div className="relative flex items-center">
                  <input
                    id={`invite-email-${index}`}
                    type="email"
                    autoComplete="off"
                    value={emailVal}
                    disabled={isSubmitting}
                    onChange={(e) => handleEmailChange(index, e.target.value)}
                    placeholder="colleague@domain.com"
                    aria-invalid={hasError}
                    aria-describedby={hasError ? `invite-error-${index}` : undefined}
                    className={`h-[42px] w-full rounded-[8px] border bg-[#0A0E18] px-3.5 text-[14px] text-white placeholder-[#94A3B8]/60 transition-all focus:outline-none focus:ring-1 ${
                      hasError
                        ? "border-[#FF4D4D] focus:ring-[#FF4D4D]"
                        : "border-[#262A35] focus:border-[#00E599] focus:ring-[#00E599]"
                    } disabled:opacity-50`}
                  />
                </div>

                {hasError && (
                  <p
                    id={`invite-error-${index}`}
                    role="alert"
                    className="text-[11px] text-[#FF4D4D] flex items-center gap-1"
                  >
                    <AlertErrorIcon className="w-3 h-3 shrink-0" />
                    <span>{fieldErrors[index]}</span>
                  </p>
                )}
              </div>
            );
          })}

          {/* Add Another Teammate Button */}
          <button
            type="button"
            onClick={handleAddInvite}
            disabled={isSubmitting || currentInvites.length >= 8}
            data-testid="add-teammate-button"
            className="flex items-center justify-center gap-1.5 h-[36px] w-full rounded-[8px] border border-dashed border-[#262A35] hover:border-[#00E599]/60 hover:bg-[#161B26] text-[12px] font-medium text-[#94A3B8] hover:text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <span>+ Add another team member</span>
          </button>
        </div>

        {/* Security Callout Box (Figma 491:1135) */}
        <div className="flex items-start gap-2.5 p-3 rounded-[8px] border border-[#262A35] bg-[#0A0E18]/80 text-[#94A3B8] text-[12px] leading-relaxed">
          <SecurityLockMintIcon className="w-4 h-4 shrink-0 text-[#00E599] mt-0.5" />
          <span>
            Invited teammates will receive one-time activation tokens valid for 48 hours to complete their account setup.
          </span>
        </div>

        {/* Action Buttons */}
        <div className="flex flex-col gap-2 mt-2 w-full">
          <button
            type="submit"
            disabled={isSubmitting}
            aria-busy={isSubmitting}
            data-testid="step3-submit-button"
            className="flex h-[44px] min-h-[44px] w-full items-center justify-center gap-2 rounded-[12px] bg-[#00E599] font-semibold text-[14px] leading-5 text-black transition-all hover:bg-[#00f5a0] active:scale-[0.99] shadow-[0px_2px_10px_rgba(0,245,160,0.15)] disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00E599] focus-visible:ring-offset-2 focus-visible:ring-offset-[#171B26]"
          >
            {isSubmitting ? (
              <span className="flex items-center gap-2">
                <svg
                  className="animate-spin -ml-1 mr-2 h-4 w-4 text-black"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  ></circle>
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  ></path>
                </svg>
                <span>Provisioning Workspace...</span>
              </span>
            ) : (
              <>
                <span>Finish & Provision Workspace</span>
                <ArrowRightMintIcon className="w-4 h-4 shrink-0" />
              </>
            )}
          </button>

          <button
            type="button"
            disabled={isSubmitting}
            data-testid="step3-skip-button"
            onClick={handleSkipClick}
            className="text-[13px] text-[#94A3B8] hover:text-[#00E599] py-1 transition-colors text-center focus-visible:outline-none focus-visible:underline disabled:opacity-50"
          >
            Skip this step for now →
          </button>

          <button
            type="button"
            disabled={isSubmitting}
            data-testid="step3-back-button"
            onClick={onBack}
            className="flex h-[42px] w-full items-center justify-center rounded-[8px] border border-[#262A35] bg-[#0A0E18] text-[13px] font-medium text-[#DFE2F1] hover:bg-[#161B26] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00E599] disabled:opacity-50"
          >
            ← Back to Administrator Account
          </button>
        </div>
      </form>
    </section>
  );
}
