/* eslint-disable @next/next/no-img-element */
import Link from "next/link";

interface PlaceholderPageProps {
  title: string;
  section: string;
  description: string;
  iconSrc: string;
  sprint?: string;
}

export function PlaceholderPage({
  title,
  section,
  description,
  iconSrc,
  sprint = "Coming Soon",
}: PlaceholderPageProps) {
  return (
    <div
      data-testid="placeholder-page"
      className="flex min-h-[calc(100vh-48px)] w-full items-center justify-center p-[24px]"
    >
      <div className="flex flex-col items-center max-w-[448px] w-full text-center">
        {/* Centered 48x48 Icon Box matching Figma 335:7712 */}
        <div className="pb-[20px] flex flex-col items-center">
          <div className="bg-[#161b26] border border-[#1f2433] border-solid flex items-center justify-center p-px rounded-[12px] shadow-[0px_1px_2px_0px_rgba(0,0,0,0.05)] size-[48px]">
            <img
              src={iconSrc}
              alt=""
              aria-hidden="true"
              className="size-[20px]"
            />
          </div>
        </div>

        {/* Section + Coming Soon Badge */}
        <div className="pb-[10px] flex items-center gap-[8px]">
          <span className="font-mono text-[10px] leading-[15px] tracking-[0.6px] uppercase text-[#7c839b]">
            {section}
          </span>
          <span className="bg-[rgba(0,229,153,0.1)] border border-[rgba(0,229,153,0.2)] border-solid px-[8px] py-[2px] rounded-[4px] font-mono text-[10px] leading-[15px] text-[#00e599]">
            {sprint}
          </span>
        </div>

        {/* Heading matching Figma 335:7718 */}
        <div className="pb-[6px] w-full">
          <h1 className="font-semibold text-[#e2e8f0] text-[16px] leading-[24px] tracking-[-0.4px]">
            {title}
          </h1>
        </div>

        {/* Description matching Figma 335:7720 */}
        <div className="pb-[24px] max-w-[360px] w-full">
          <p className="font-normal text-[#7c839b] text-[12px] leading-[19.5px]">
            {description}
          </p>
        </div>

        {/* Action Button matching Figma 335:7722 */}
        <Link
          href="/"
          data-testid="back-to-overview-button"
          className="bg-[#161b26] border border-[#1f2433] border-solid flex items-center justify-center px-[16px] py-[8px] rounded-[6px] font-medium text-[#e2e8f0] text-[12px] leading-[16px] hover:border-[#00e599]/50 transition-colors"
        >
          Back to Overview
        </Link>
      </div>
    </div>
  );
}
