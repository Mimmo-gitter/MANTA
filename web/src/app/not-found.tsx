/* eslint-disable @next/next/no-img-element */
import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-screen w-full flex-col items-center justify-center bg-[#0f131d] px-[24px] text-[#e2e8f0]">
      <div className="bg-[#161b26] border border-[#1f2433] border-solid flex flex-col items-center max-w-[448px] w-full p-[32px] rounded-[12px] shadow-[0px_20px_25px_-5px_rgba(0,0,0,0.4)] text-center">
        <div className="pb-[16px]">
          <img
            src="/icons/shell/f14d9.png"
            alt="M.A.N.T.A."
            className="size-[36px] object-contain"
          />
        </div>

        <div className="pb-[8px]">
          <span className="bg-[rgba(239,68,68,0.1)] border border-[rgba(239,68,68,0.2)] border-solid px-[8px] py-[2px] rounded-[4px] font-mono text-[10px] leading-[15px] text-[#ef4444]">
            404 NOT FOUND
          </span>
        </div>

        <h1 className="font-semibold text-[#e2e8f0] text-[18px] leading-[28px] tracking-[-0.45px] pb-[8px]">
          Page Not Found
        </h1>

        <p className="font-normal text-[#7c839b] text-[12px] leading-[19.5px] pb-[24px] max-w-[340px]">
          The route you navigated to does not exist in this workspace or may have been relocated.
        </p>

        <Link
          href="/"
          data-testid="not-found-back-home"
          className="bg-[#00e599] flex items-center justify-center px-[16px] py-[8px] rounded-[6px] shadow-[0px_0px_16px_0px_rgba(0,229,153,0.2)] font-semibold text-[#0f131d] text-[12px] leading-[16px] hover:brightness-105 transition-all"
        >
          Back to Overview
        </Link>
      </div>
    </div>
  );
}
