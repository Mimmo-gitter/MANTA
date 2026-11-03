/* eslint-disable @next/next/no-img-element */
"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAVIGATION_SECTIONS } from "@/config/navigation";
import { useShellState } from "@/components/layout/shell-context";
import { cn } from "@/lib/utils";
import {
  FigmaOverviewIcon,
  FigmaProjectsIcon,
  FigmaDoraIcon,
  FigmaAlertsIcon,
  FigmaSprintIcon,
  FigmaScopeCreepIcon,
  FigmaAdrIcon,
  FigmaSemanticSearchIcon,
  FigmaVerificationIcon,
  FigmaSettingsIcon,
  FigmaIntegrationsIcon,
  FigmaMembersIcon,
  FigmaTrashIcon,
  FigmaActiveChevron,
  FigmaMantaEmblem,
} from "../ui/figma-icons";

const FIGMA_NAV_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  overview: FigmaOverviewIcon,
  projects: FigmaProjectsIcon,
  "delivery-analytics": FigmaDoraIcon,
  "risks-alerts": FigmaAlertsIcon,
  "sprint-wip": FigmaSprintIcon,
  "scope-creep": FigmaScopeCreepIcon,
  "adr-knowledge-base": FigmaAdrIcon,
  "semantic-search": FigmaSemanticSearchIcon,
  "refactoring-verification": FigmaVerificationIcon,
  "workspace-settings": FigmaSettingsIcon,
  integrations: FigmaIntegrationsIcon,
  "access-roles": FigmaMembersIcon,
  "privacy-erasure": FigmaTrashIcon,
  "adr-governance": FigmaVerificationIcon,
};

interface SidebarProps {
  mobileOpen?: boolean;
  onMobileClose?: () => void;
  isTablet?: boolean;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

export function Sidebar({
  mobileOpen = false,
  onMobileClose,
  isTablet = false,
  collapsed = false,
  onToggleCollapse,
}: SidebarProps) {
  const pathname = usePathname();
  const { shellState } = useShellState();
  const [logoHovered, setLogoHovered] = useState(false);

  const isCurrentRoute = (href: string) => {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  const isItemActive = (href: string) => {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  const projectsBadge =
    pathname === "/" && (shellState === "empty" || shellState === "error" || shellState === "loading")
      ? "0"
      : "12";

  // ---------------------------------------------------------------------------
  // 1. TABLET COLLAPSED SIDEBAR (72px wide — Figma 338:12871)
  // ---------------------------------------------------------------------------
  if (isTablet && collapsed && !mobileOpen) {
    return (
      <aside
        id="portal-sidebar"
        data-testid="portal-sidebar"
        aria-label="Sidebar Navigation"
        className="bg-[#171b26] border-[#1c2433] border-r border-solid flex flex-col h-full items-center justify-between pr-px py-[12px] relative shrink-0 w-[72px] z-[2]"
      >
        <div className="relative shrink-0 w-full">
          <div className="flex flex-col gap-[10px] items-center relative size-full">
            {/* M.A.N.T.A. Emblem Mark Button (40x40px) */}
            <div className="relative flex items-center justify-center">
              <button
                type="button"
                onClick={onToggleCollapse}
                onMouseEnter={() => setLogoHovered(true)}
                onMouseLeave={() => setLogoHovered(false)}
                onFocus={() => setLogoHovered(true)}
                onBlur={() => setLogoHovered(false)}
                aria-label="Expand sidebar navigation"
                data-testid="sidebar-collapse-toggle"
                className="bg-[#0a0e18] border border-[rgba(0,229,153,0.3)] border-solid drop-shadow-[0px_0px_6px_rgba(0,229,153,0.15)] flex items-center justify-center p-px relative rounded-[8px] shrink-0 size-[40px] cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]"
              >
                <div className="h-[40px] relative shrink-0 w-[39px] overflow-hidden pointer-events-none">
                  <img
                    alt="M.A.N.T.A."
                    className="block max-w-none size-full object-contain"
                    src="/icons/shell/9933d.png"
                  />
                </div>
              </button>

              {logoHovered && (
                <div
                  role="tooltip"
                  className="pointer-events-none absolute left-[48px] top-1/2 -translate-y-1/2 z-50 backdrop-blur-[12px] bg-[rgba(10,14,24,0.95)] border border-[rgba(0,229,153,0.6)] border-solid flex gap-[6px] items-center px-[9px] py-[5px] rounded-[2px] shadow-[0px_4px_16px_0px_rgba(0,0,0,0.6)] whitespace-nowrap"
                >
                  <div className="bg-[#00e599] rounded-[12px] shrink-0 size-[6px]" />
                  <span className="font-mono font-semibold text-[#00e599] text-[9px] leading-[13.5px] tracking-[-0.225px]">
                    Click Logo to Expand / Collapse
                  </span>
                </div>
              )}
            </div>

            {/* Collapsed Navigation: 14 Icons across 3 groups */}
            <nav
              aria-label="Primary Navigation"
              className="flex flex-col gap-[4px] items-center px-[8px] relative shrink-0 w-full"
            >
              {NAVIGATION_SECTIONS.map((section, sectionIdx) => (
                <div key={section.id} className="flex flex-col gap-[4px] items-center w-full">
                  {sectionIdx > 0 && (
                    <div className="flex flex-col h-[9px] items-start py-[4px] relative shrink-0 w-[32px]">
                      <div className="bg-[#1c2433] h-px opacity-80 shrink-0 w-[32px]" />
                    </div>
                  )}
                  <div className="flex flex-col gap-[4px] items-center relative shrink-0 w-full">
                    {section.items.map((item) => {
                      const active = isItemActive(item.href);
                      const IconComponent = FIGMA_NAV_ICONS[item.id] || FigmaOverviewIcon;
                      return (
                        <Link
                          key={item.id}
                          href={item.href}
                          title={item.label}
                          aria-label={item.label}
                          data-testid={`nav-link-${item.id}`}
                          aria-current={isCurrentRoute(item.href) ? "page" : undefined}
                          className={cn(
                            "flex items-center justify-center relative rounded-[4px] shrink-0 size-[40px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                            active
                              ? "bg-[rgba(0,229,153,0.15)] border border-[rgba(0,229,153,0.3)] border-solid p-px shadow-[0px_0px_10px_0px_rgba(0,229,153,0.2)] text-[#00e599]"
                              : "text-[#64748b] hover:bg-white/[0.04] hover:text-[#94a3b8]",
                          )}
                        >
                          <IconComponent className="shrink-0 size-[14px]" />
                        </Link>
                      );
                    })}
                  </div>
                </div>
              ))}
            </nav>
          </div>
        </div>
      </aside>
    );
  }

  // ---------------------------------------------------------------------------
  // 2. TABLET EXPANDED SIDEBAR (240px wide — Figma 340:13023)
  // ---------------------------------------------------------------------------
  if (isTablet && !collapsed && !mobileOpen) {
    return (
      <aside
        id="portal-sidebar"
        data-testid="portal-sidebar"
        aria-label="Sidebar Navigation"
        className="bg-[#171b26] border-[#1c2433] border-r border-solid flex flex-col h-full items-center justify-between overflow-clip pr-px py-[12px] relative shrink-0 w-[240px] z-[2]"
      >
        <div className="relative shrink-0 w-full">
          <div className="flex flex-col gap-[8px] items-center relative size-full">
            {/* M.A.N.T.A. Hexagonal Emblem Mark as Toggle Button */}
            <div className="flex items-center px-[16px] relative shrink-0 w-full">
              <div className="flex flex-1 gap-[12px] items-center min-w-0 relative">
                {/* Tooltip & Pointer Overlay from Figma 340:13029 */}
                {logoHovered && (
                  <div
                    role="tooltip"
                    className="pointer-events-none absolute bottom-[-20px] left-[-4px] z-50 flex flex-col items-start"
                  >
                    <div className="h-[19.5px] relative shrink-0 w-[206.44px]">
                      <div className="absolute backdrop-blur-[12px] bg-[rgba(10,14,24,0.95)] border border-[rgba(0,229,153,0.6)] border-solid flex gap-[6px] items-center left-[16px] px-[9px] py-[5px] rounded-[2px] shadow-[0px_4px_16px_0px_rgba(0,0,0,0.6)] top-[-4px]">
                        <div className="bg-[#00e599] relative rounded-[12px] shrink-0 size-[6px]" />
                        <span className="font-mono font-semibold text-[#00e599] text-[9px] leading-[13.5px] tracking-[-0.225px] whitespace-nowrap">
                          Click Logo to Expand / Collapse
                        </span>
                      </div>
                    </div>
                  </div>
                )}

                <button
                  type="button"
                  onClick={onToggleCollapse}
                  onMouseEnter={() => setLogoHovered(true)}
                  onMouseLeave={() => setLogoHovered(false)}
                  onFocus={() => setLogoHovered(true)}
                  onBlur={() => setLogoHovered(false)}
                  aria-label="Collapse sidebar navigation"
                  data-testid="sidebar-collapse-toggle"
                  className="bg-[#0a0e18] border border-[#00e599] border-solid drop-shadow-[0px_0px_8px_rgba(0,229,153,0.35)] flex items-center justify-center min-w-[40px] p-px relative rounded-[8px] shrink-0 size-[40px] cursor-pointer focus-visible:outline-none"
                >
                  <div className="relative shrink-0 size-[40px] overflow-hidden pointer-events-none">
                    <img
                      alt="M.A.N.T.A."
                      className="block max-w-none size-full object-contain"
                      src="/icons/shell/9933d.png"
                    />
                  </div>
                  <div className="pointer-events-none absolute border border-[rgba(0,229,153,0.6)] border-solid inset-[-6px] opacity-60 rounded-[16px]" />
                  <div className="pointer-events-none absolute border border-[rgba(0,194,200,0.3)] border-solid inset-[-12px] rounded-[16px]" />
                </button>

                <div className="flex flex-1 flex-col items-start justify-center max-w-[160px] min-w-0 overflow-clip relative">
                  <span className="font-mono font-semibold text-[#d0d2f1] text-[13px] leading-[13px] tracking-[0.65px] whitespace-nowrap">
                    M.A.N.T.A.
                  </span>
                  <span className="font-sans font-normal text-[#a4a8b8] text-[10px] leading-[10px] pt-[4px]">
                    v2.4.0
                  </span>
                </div>
              </div>
            </div>

            {/* Navigation Links across 3 groups (Figma 340:13055) */}
            <nav
              aria-label="Primary Navigation"
              className="flex flex-col items-start pt-[4px] relative shrink-0 w-full"
            >
              <div className="flex flex-col gap-[2px] items-start overflow-x-clip overflow-y-auto px-[12px] relative shrink-0 w-full">
                {NAVIGATION_SECTIONS.map((section, sectionIdx) => (
                  <div key={section.id} className="flex flex-col w-full">
                    {sectionIdx > 0 && (
                      <div className="flex flex-col h-[9px] items-start py-[4px] relative shrink-0 w-full">
                        <div className="bg-[#1c2433] h-px opacity-70 relative shrink-0 w-full" />
                      </div>
                    )}
                    <div className="flex flex-col items-start max-h-[30px] pb-[2px] pt-[4px] relative shrink-0 w-full">
                      <div className="flex flex-col items-start max-h-[24px] px-[8px] relative shrink-0 w-full">
                        <span className="font-mono font-semibold text-[10px] leading-[15px] text-[rgba(164,168,184,0.6)] tracking-[0.5px] uppercase w-full">
                          {section.title}
                        </span>
                      </div>
                    </div>
                    <div className="flex flex-col gap-[4px] items-start relative shrink-0 w-full">
                      {section.items.map((item) => {
                        const active = isItemActive(item.href);
                        const IconComponent = FIGMA_NAV_ICONS[item.id] || FigmaOverviewIcon;
                        return (
                          <Link
                            key={item.id}
                            href={item.href}
                            data-testid={`nav-link-${item.id}`}
                            aria-current={isCurrentRoute(item.href) ? "page" : undefined}
                            className={cn(
                              "flex h-[36px] items-center justify-between relative rounded-[4px] shrink-0 w-full transition-colors",
                              active
                                ? "bg-[rgba(0,229,153,0.15)] border border-[rgba(0,229,153,0.3)] border-solid px-[9px] py-px shadow-[0px_0px_10px_0px_rgba(0,229,153,0.2)]"
                                : "px-[8px] hover:bg-white/[0.04]",
                            )}
                          >
                            <div className="flex items-center gap-[10px] min-w-0">
                              <IconComponent
                                className={cn(
                                  "shrink-0 size-[14px]",
                                  active ? "text-[#00e599]" : "text-[#64748b]",
                                )}
                              />
                              <span
                                className={cn(
                                  "font-sans font-medium text-[12px] leading-[18px] whitespace-nowrap",
                                  active ? "text-[#00e599]" : "text-[#a4a8b8]",
                                )}
                              >
                                {item.tabletLabel}
                              </span>
                            </div>

                            <div className="flex items-center gap-[6px] shrink-0">
                              {item.id === "projects" && (
                                <div className="bg-[#0a0e18] border border-[#1c2433] border-solid flex flex-col items-start px-[7px] py-px relative rounded-[2px] shrink-0">
                                  <span className="font-mono font-normal text-[#a4a8b8] text-[10px] leading-[15px] whitespace-nowrap">
                                    {projectsBadge}
                                  </span>
                                </div>
                              )}
                              {active && (
                                <FigmaActiveChevron className="shrink-0" aria-hidden="true" />
                              )}
                            </div>
                          </Link>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </nav>
          </div>
        </div>
      </aside>
    );
  }

  // ---------------------------------------------------------------------------
  // 3. DESKTOP PERSISTENT SIDEBAR (216px wide — Figma 325:2340 / 335:7565 / 336:8004 / 337:11533)
  //    & Mobile Off-Canvas Drawer
  // ---------------------------------------------------------------------------
  return (
    <>
      {mobileOpen && (
        <div
          data-testid="sidebar-mobile-backdrop"
          onClick={onMobileClose}
          className="fixed inset-0 z-40 bg-black/70 backdrop-blur-sm md:hidden"
          aria-hidden="true"
        />
      )}

      <aside
        id="portal-sidebar"
        data-testid="portal-sidebar"
        aria-label="Sidebar Navigation"
        className={cn(
          "bg-[#0f131d] border-[rgba(37,43,59,0.6)] border-r border-solid h-full relative shrink-0 w-[216px] flex flex-col justify-between overflow-clip pr-px select-none",
          "fixed top-0 bottom-0 left-0 z-50 md:static md:z-auto transition-transform duration-200 md:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
        )}
      >
        {/* Top Section: Brand Header & Nav */}
        <div className="flex-1 min-h-0 flex flex-col items-start overflow-clip relative w-full">
          {/* Brand Header (Compact height 48px — Figma 325:2707) */}
          <div className="bg-[rgba(23,27,38,0.3)] border-[rgba(37,43,59,0.6)] border-b border-solid flex gap-[8px] h-[48px] items-center pb-px px-[14px] relative shrink-0 w-full">
            <button
              type="button"
              onClick={onToggleCollapse}
              onMouseEnter={() => setLogoHovered(true)}
              onMouseLeave={() => setLogoHovered(false)}
              aria-label="Toggle sidebar navigation"
              data-testid="sidebar-collapse-toggle"
              className="border border-[#252b3b] border-solid relative rounded-[4px] shrink-0 size-[20px] flex items-center justify-center p-px bg-[#0a0e18] cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]"
            >
              <div className="border border-[#262c3d] border-solid pointer-events-none relative rounded-[6px] shrink-0 size-[24px] bg-[#0a0e18] overflow-hidden shadow-[inset_0px_2px_4px_0px_rgba(0,0,0,0.05)]">
                <img
                  alt="M.A.N.T.A. Logo"
                  className="block max-w-none size-full object-cover"
                  src="/icons/shell/f14d9.png"
                />
              </div>
            </button>

            <Link
              href="/"
              className="flex flex-col gap-px items-start relative shrink-0 focus-visible:outline-none"
            >
              <span className="font-sans font-bold text-[#dfe2f1] text-[13px] leading-[13px] tracking-[0.65px] whitespace-nowrap">
                M.A.N.T.A.
              </span>
              <span className="font-mono font-normal text-[#64748b] text-[9px] leading-[11.25px] tracking-[-0.225px] whitespace-nowrap">
                v2.4.0
              </span>
            </Link>
          </div>

          {/* Navigation Body (High Density, 28px nav items — Figma 325:2715) */}
          <nav
            aria-label="Primary Navigation"
            className="flex flex-1 flex-col gap-[11px] items-start min-h-0 overflow-y-auto pt-[7px] pb-[16px] px-[8px] relative w-full"
          >
            {NAVIGATION_SECTIONS.map((section) => (
              <div
                key={section.id}
                className="flex flex-col gap-[4px] items-start relative shrink-0 w-full"
              >
                <div className="flex flex-col items-start px-[8px] relative shrink-0 w-full">
                  <span
                    id={`nav-section-${section.id}`}
                    className="font-mono font-semibold text-[#64748b] text-[9px] leading-[13.5px] tracking-[0.45px] uppercase w-full"
                  >
                    {section.title}
                  </span>
                </div>

                <div
                  role="group"
                  aria-labelledby={`nav-section-${section.id}`}
                  className="flex flex-col gap-[2px] items-start relative shrink-0 w-full"
                >
                  {section.items.map((item) => {
                    const active = isItemActive(item.href);
                    const IconComponent = FIGMA_NAV_ICONS[item.id] || FigmaOverviewIcon;
                    const showOverviewErrorDot =
                      item.id === "overview" && pathname === "/" && shellState === "error";
                    const showRisksAlertDot =
                      item.id === "risks-alerts" &&
                      !(pathname === "/" && (shellState === "empty" || shellState === "error" || shellState === "loading"));

                    return (
                      <Link
                        key={item.id}
                        href={item.href}
                        onClick={onMobileClose}
                        data-testid={`nav-link-${item.id}`}
                        aria-current={isCurrentRoute(item.href) ? "page" : undefined}
                        className={cn(
                          "flex h-[28px] items-center justify-between px-[8px] relative rounded-[6px] shrink-0 w-full transition-colors",
                          active
                            ? "bg-[rgba(0,229,153,0.1)] border border-[rgba(0,229,153,0.4)] border-solid"
                            : "border border-transparent hover:bg-white/[0.03]",
                        )}
                      >
                        <div className="flex gap-[8px] items-center overflow-clip relative shrink-0">
                          <IconComponent
                            className={cn(
                              "shrink-0 size-[14px]",
                              active ? "text-[#00e599]" : "text-[#64748b]",
                            )}
                          />
                          <span
                            className={cn(
                              "font-sans font-medium text-[12px] leading-[18px] whitespace-nowrap",
                              active ? "text-[#00e599]" : "text-[#94a3b8]",
                            )}
                          >
                            {item.label}
                          </span>
                        </div>

                        <div className="flex items-center gap-[6px] shrink-0">
                          {item.id === "projects" && (
                            <div className="bg-[#0a0e18] border border-[#252b3b] border-solid flex flex-col items-start px-[5px] py-[3px] relative rounded-[4px] shrink-0">
                              <span className="font-mono font-medium text-[#94a3b8] text-[9px] leading-[9px] whitespace-nowrap">
                                {projectsBadge}
                              </span>
                            </div>
                          )}

                          {(showOverviewErrorDot || showRisksAlertDot) && (
                            <div className="bg-[#ff4d4d] relative rounded-full shrink-0 size-[6px]" />
                          )}

                          {active && (
                            <FigmaActiveChevron className="shrink-0" aria-hidden="true" />
                          )}
                        </div>
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>
        </div>
      </aside>
    </>
  );
}
