"use client";

import React, { useState } from "react";
import { useSearchParams } from "next/navigation";
import { RegisterStep1Card, Step1Data } from "@/components/auth/register-step1-card";
import { RegisterStep2Card, Step2Data } from "@/components/auth/register-step2-card";
import { RegisterStep3Card, Step3Data } from "@/components/auth/register-step3-card";
import { RegisterSuccessCard } from "@/components/auth/register-success-card";

export type RegisterStep = 1 | 2 | 3 | "success";

export interface RegisterFormData extends Step1Data, Step2Data, Step3Data {}

export function RegisterContent() {
  const searchParams = useSearchParams();

  const [step, setStep] = useState<RegisterStep>(1);
  const [formData, setFormData] = useState<RegisterFormData>({
    organizationName: searchParams.get("name") || "",
    tenantSlug: searchParams.get("slug") || "",
    industry: searchParams.get("industry") || "",
    adminName: searchParams.get("adminName") || "",
    adminEmail: searchParams.get("email") || "",
    password: "",
    confirmPassword: "",
    invites: ["", ""],
  });

  const [duplicateError, setDuplicateError] = useState<string | null>(null);
  const [generalError, setGeneralError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [registeredResult, setRegisteredResult] = useState<{
    tenant_id?: string;
    slug: string;
    name: string;
    admin_email: string;
  } | null>(null);

  const updateFormData = (updated: Partial<RegisterFormData>) => {
    setFormData((prev) => ({ ...prev, ...updated }));
  };

  const submitRegistration = async (teamInvites: string[]) => {
    if (isSubmitting) return;

    setIsSubmitting(true);
    setGeneralError(null);
    setDuplicateError(null);

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

    const payload = {
      name: formData.organizationName.trim(),
      slug: formData.tenantSlug.trim(),
      admin_name: formData.adminName.trim(),
      admin_email: formData.adminEmail.trim().toLowerCase(),
      allowed_domain: formData.adminEmail.split("@")[1] || undefined,
    };

    try {
      const response = await fetch(`${apiUrl}/tenants/register`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (response.status === 409) {
        // Figma 491:1188: duplicate detected, navigate back to step 1
        const conflictData = await response.json().catch(() => null);
        const detailMsg =
          conflictData?.detail ||
          "Organization name or tenant slug is already taken. Please choose a different slug.";
        setDuplicateError(detailMsg);
        setStep(1);
        setIsSubmitting(false);
        return;
      }

      if (response.status === 422) {
        const errorData = await response.json().catch(() => null);
        let errorDetail = "Invalid registration data. Please verify your fields.";
        if (Array.isArray(errorData?.detail)) {
          errorDetail = errorData.detail.map((err: any) => err.msg || err.message).join(", ");
        } else if (typeof errorData?.detail === "string") {
          errorDetail = errorData.detail;
        }
        setGeneralError(errorDetail);
        setIsSubmitting(false);
        return;
      }

      if (!response.ok) {
        setGeneralError(
          "We could not provision your workspace due to a server error. Please try again or contact support."
        );
        setIsSubmitting(false);
        return;
      }

      const result = await response.json();
      setRegisteredResult({
        tenant_id: result.tenant_id,
        slug: result.slug || formData.tenantSlug,
        name: result.name || formData.organizationName,
        admin_email: result.admin_email || formData.adminEmail,
      });
      setStep("success");
    } catch (err: any) {
      setGeneralError(
        "Network connection failed. Please ensure the backend service is running and try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="w-full flex justify-center items-center">
      {step === 1 && (
        <RegisterStep1Card
          data={{
            organizationName: formData.organizationName,
            tenantSlug: formData.tenantSlug,
            industry: formData.industry,
          }}
          onChange={updateFormData}
          onNext={() => setStep(2)}
          duplicateError={duplicateError}
          onClearDuplicateError={() => setDuplicateError(null)}
        />
      )}

      {step === 2 && (
        <RegisterStep2Card
          data={{
            adminName: formData.adminName,
            adminEmail: formData.adminEmail,
            password: formData.password,
            confirmPassword: formData.confirmPassword,
          }}
          organizationName={formData.organizationName}
          onChange={updateFormData}
          onNext={() => setStep(3)}
          onBack={() => setStep(1)}
        />
      )}

      {step === 3 && (
        <RegisterStep3Card
          data={{
            invites: formData.invites,
          }}
          organizationName={formData.organizationName}
          onChange={updateFormData}
          onSubmit={() => submitRegistration(formData.invites)}
          onSkip={() => submitRegistration([])}
          onBack={() => setStep(2)}
          isSubmitting={isSubmitting}
          error={generalError}
        />
      )}

      {step === "success" && (
        <RegisterSuccessCard
          organizationName={registeredResult?.name || formData.organizationName}
          slug={registeredResult?.slug || formData.tenantSlug}
          adminName={formData.adminName}
          adminEmail={registeredResult?.admin_email || formData.adminEmail}
        />
      )}
    </div>
  );
}
