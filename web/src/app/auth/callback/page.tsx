import { Suspense } from "react";
import { CallbackContent } from "./callback-content";

export default function AuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-[#0F121C] text-white">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-purple-500 border-t-transparent" />
            <p className="text-sm text-slate-400">Verifying security credentials...</p>
          </div>
        </div>
      }
    >
      <CallbackContent />
    </Suspense>
  );
}
