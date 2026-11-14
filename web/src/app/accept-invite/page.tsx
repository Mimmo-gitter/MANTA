import { Suspense } from "react";
import { AcceptInviteContent } from "@/app/accept-invite/accept-invite-content";

export default function AcceptInvitePage() {
  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen w-full items-center justify-center bg-[#070a11] text-[#94a3b8]">
          Loading invitation...
        </main>
      }
    >
      <AcceptInviteContent />
    </Suspense>
  );
}
