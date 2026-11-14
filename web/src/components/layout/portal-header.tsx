/* eslint-disable @next/next/no-img-element */
"use client";

import { useState, useRef, useEffect, useId } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Menu } from "lucide-react";
import { useAuth } from "@/providers/auth-provider";
import { usePermissions } from "@/hooks/use-permissions";
import { getPrimaryRoleLabel } from "@/lib/permissions";
import { setIsLoggingOut } from "@/lib/auth";
import { sidebarSections } from "@/config/navigation";
import { cn } from "@/lib/utils";

interface PortalHeaderProps {
  onMobileMenuToggle?: () => void;
  isTabletCollapsed?: boolean;
  isTablet?: boolean;
}

const ENV_LABEL = process.env.NEXT_PUBLIC_ENV_LABEL || "PRODUCTION";

export function PortalHeader({
  onMobileMenuToggle,
  isTablet = false,
}: PortalHeaderProps) {
  const { isAuthenticated, displayName, email, tenantName, logout } = useAuth();
  const { roles } = usePermissions();
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [projectSearch, setProjectSearch] = useState("");
  const pathname = usePathname();
  const router = useRouter();

  const effectiveDisplayName =
    displayName && displayName !== "User"
      ? displayName
      : email || "Authenticated User";
  const effectiveEmail = email;
  const roleLabel = getPrimaryRoleLabel(roles);

  const workspaceRef = useRef<HTMLDivElement>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const desktopSearchInputId = useId();
  const tabletSearchInputId = useId();
  const [isSignedOut, setIsSignedOut] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setIsSignedOut(window.sessionStorage.getItem("user_signed_out") === "true");
    }
  }, [pathname, isAuthenticated]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (pathname === "/projects") {
      const q = new URLSearchParams(window.location.search).get("q") ?? "";
      setProjectSearch(q);
    } else {
      setProjectSearch("");
    }
  }, [pathname]);

  const showProfile = isAuthenticated && !isSignedOut;

  // Close dropdowns when clicking outside or pressing Escape
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        workspaceRef.current &&
        !workspaceRef.current.contains(event.target as Node)
      ) {
        setWorkspaceMenuOpen(false);
      }
      if (
        userMenuRef.current &&
        !userMenuRef.current.contains(event.target as Node)
      ) {
        setUserMenuOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setWorkspaceMenuOpen(false);
        setUserMenuOpen(false);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  const handleSignOut = () => {
    setIsLoggingOut(true);
    if (typeof window !== "undefined") {
      window.sessionStorage.setItem("user_signed_out", "true");
    }
    const workspaceQuery =
      currentWorkspaceName && currentWorkspaceName !== "Active Workspace"
        ? `?workspace=${encodeURIComponent(currentWorkspaceName)}`
        : "";
    router.replace(`/signed-out${workspaceQuery}`);

    if (isAuthenticated) {
      logout();
    }
  };

  // Compute initials for avatar
  const getInitials = (name?: string | null) => {
    if (!name) return "?";
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }
    return name.slice(0, 2).toUpperCase();
  };

  const currentWorkspaceName =
    tenantName && tenantName !== "Unknown Tenant"
      ? tenantName
      : "Active Workspace";

  const handleProjectSearchSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = projectSearch.trim();
    router.push(trimmed ? `/projects?q=${encodeURIComponent(trimmed)}` : "/projects");
  };

  // Match exact sidebar label for breadcrumb
  const getBreadcrumbInfo = () => {
    for (const section of sidebarSections) {
      for (const item of section.items) {
        if (
          item.href === "/"
            ? pathname === "/"
            : pathname === item.href || pathname.startsWith(`${item.href}/`)
        ) {
          return {
            section:
              section.title.charAt(0) + section.title.slice(1).toLowerCase(),
            title: item.label,
          };
        }
      }
    }
    return { section: "Workspace", title: "Overview" };
  };

  const breadcrumb = getBreadcrumbInfo();

  /* ------------------------------------------------------------------ */
  /*Workspace Dropdown Content (Figma 334:5977 - 1-to-1)                 */
  /* ------------------------------------------------------------------ */
  const renderWorkspaceDropdown = () =>
    workspaceMenuOpen && (
      <div
        data-testid="workspace-dropdown-menu"
        role="menu"
        aria-label="Workspace Switcher"
        className="absolute left-0 top-[calc(100%+8px)] w-[320px] rounded-[12px] border border-[#1f2433] bg-[#161b26] p-px shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.7)] z-50 flex flex-col overflow-hidden"
      >
        {/* Header */}
        <div className="border-b border-[#1f2433] px-[14px] py-[10px]">
          <div className="font-mono text-[10px] leading-[15px] text-[#7c839b] tracking-[0.5px] uppercase">
            Active Workspace
          </div>
        </div>

        {/* Current Tenant Workspace */}
        <div className="p-[6px] flex flex-col gap-[6px]" role="none">
          <button
            type="button"
            role="menuitem"
            onClick={() => setWorkspaceMenuOpen(false)}
            className="flex w-full items-center justify-between rounded-[8px] text-left bg-[rgba(0,229,153,0.1)] border border-[rgba(0,229,153,0.2)] px-[11px] py-[9px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]"
          >
            <div className="flex items-center gap-[10px] min-w-0">
              <div className="bg-[rgba(0,229,153,0.2)] flex size-[28px] shrink-0 items-center justify-center rounded-[4px]">
                <img
                  src="/icons/shell/lightning-emerald.svg"
                  alt=""
                  aria-hidden="true"
                  className="h-[14px] w-[8px]"
                />
              </div>
              <div className="flex flex-col min-w-0">
                <span className="text-[12px] font-medium leading-[16px] text-[#e2e8f0] truncate">
                  {currentWorkspaceName}
                </span>
                <span className="font-mono text-[10px] leading-[15px] text-[#00e599]">
                  Active
                </span>
              </div>
            </div>

            <div
              className="bg-[#00e599] rounded-full size-[6px] shadow-[0px_0px_6px_0px_#00e599] shrink-0"
              aria-hidden="true"
            />
          </button>

          <p className="px-[8px] pb-[4px] font-sans text-[11px] leading-[15px] text-[#7c839b]">
            Multi-workspace switching requires cross-tenant session support.
          </p>
        </div>
      </div>
    );

  /* ------------------------------------------------------------------ */
  /* User Profile Dropdown Content (Figma 321:1453 - 1-to-1)             */
  /* ------------------------------------------------------------------ */
  const renderUserDropdown = () =>
    userMenuOpen && (
      <div
        data-testid="user-dropdown-menu"
        role="menu"
        aria-label="User Profile Dropdown"
        className="absolute right-0 top-[calc(100%+8px)] w-[256px] rounded-[12px] border border-[#1f2433] bg-[#161b26] p-px shadow-[0px_25px_50px_-12px_rgba(0,0,0,0.7)] z-50 flex flex-col overflow-hidden"
      >
        {/* Header (Figma 321:1455) */}
        <div className="border-b border-[#1f2433] pt-[14px] pb-[15px] px-[14px] flex flex-col gap-[8px]">
          <div className="flex items-center gap-[10px]">
            <div className="bg-[rgba(0,229,153,0.15)] border border-[rgba(0,229,153,0.3)] flex size-[36px] shrink-0 items-center justify-center rounded-[8px] p-px">
              <span className="font-bold text-[#00e599] text-[12px] leading-[16px]">
                {getInitials(effectiveDisplayName)}
              </span>
            </div>
            <div className="flex flex-col min-w-0">
              <span className="font-semibold text-[#e2e8f0] text-[12px] leading-[16px] truncate">
                {effectiveDisplayName}
              </span>
              {effectiveEmail && (
                <span className="font-mono text-[#7c839b] text-[10px] leading-[15px] truncate">
                  {effectiveEmail}
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center">
            <span className="bg-[rgba(0,229,153,0.1)] border border-[rgba(0,229,153,0.2)] inline-flex items-center px-[9px] py-[3px] rounded-[4px] font-mono text-[#00e599] text-[9px] leading-[13.5px] tracking-[0.45px] uppercase">
              {roleLabel}
            </span>
          </div>
        </div>

        {/* Navigation Links (Figma 321:1469) */}
        <div className="p-[6px] flex flex-col gap-[2px]" role="none">
          <Link
            href="/workspace-settings"
            role="menuitem"
            onClick={() => setUserMenuOpen(false)}
            className="flex items-center gap-[10px] px-[10px] py-[8px] rounded-[8px] text-[12px] font-medium leading-[16px] text-[#e2e8f0] hover:bg-white/[0.04] transition-colors"
          >
            <img
              src="/icons/shell/5da87.svg"
              alt=""
              aria-hidden="true"
              className="size-[14px] shrink-0"
            />
            <span>My Profile</span>
          </Link>

          <Link
            href="/workspace-settings"
            role="menuitem"
            onClick={() => setUserMenuOpen(false)}
            className="flex items-center gap-[10px] px-[10px] py-[8px] rounded-[8px] text-[12px] font-medium leading-[16px] text-[#e2e8f0] hover:bg-white/[0.04] transition-colors"
          >
            <img
              src="/icons/shell/91821.svg"
              alt=""
              aria-hidden="true"
              className="size-[14px] shrink-0"
            />
            <span>Account Settings</span>
          </Link>
        </div>

        {/* Sign Out Section (Figma 321:1480) */}
        <div className="border-t border-[#1f2433] pt-[7px] pb-[6px] px-[6px]" role="none">
          <button
            type="button"
            role="menuitem"
            data-testid="dropdown-signout-button"
            onClick={() => {
              setUserMenuOpen(false);
              handleSignOut();
            }}
            className="flex w-full items-center gap-[10px] px-[10px] py-[8px] rounded-[8px] text-left text-[12px] font-medium leading-[16px] text-[#ef4444] hover:bg-[#ef4444]/10 transition-colors"
          >
            <img
              src="/icons/shell/64a19.svg"
              alt=""
              aria-hidden="true"
              className="size-[14px] shrink-0"
            />
            <span>Sign Out</span>
          </button>
        </div>
      </div>
    );

  /* ------------------------------------------------------------------ */
  /* TABLET HEADER (Figma 338:12871 & 340:13023 - 1-to-1)               */
  /* ------------------------------------------------------------------ */
  if (isTablet) {
    return (
      <header
        data-testid="portal-header"
        role="banner"
        aria-label="Application Header"
        className={cn(
          "backdrop-blur-[6px] bg-[#171B26] border-b border-[#252B3B] border-solid flex h-[56px] items-center px-[20px] shrink-0 w-full z-30",
          "justify-between max-[768px]:justify-end",
        )}
      >
        {/* Left: Workspace / Overview Breadcrumb */}
        <nav
          aria-label="Header Breadcrumb"
          className="hidden min-[769px]:flex gap-[8px] items-center"
        >
          <span className="font-normal text-[#8b95a5] text-[14px] leading-[20px]">
            {breadcrumb.section}
          </span>
          <span className="font-normal text-[rgba(139,149,165,0.4)] text-[14px] leading-[20px]">
            /
          </span>
          <span className="font-medium text-[#f0f4f8] text-[14px] leading-[20px]">
            {breadcrumb.title}
          </span>
        </nav>

        {/* Right: Controls Row */}
        <div className="flex gap-[10px] items-center">
          {/* Workspace Selector */}
          <div ref={workspaceRef} className="relative">
            <button
              type="button"
              data-testid="workspace-selector-trigger"
              onClick={() => setWorkspaceMenuOpen(!workspaceMenuOpen)}
              aria-expanded={workspaceMenuOpen}
              aria-haspopup="menu"
              aria-label={`Current Workspace: ${currentWorkspaceName}. Click to switch.`}
              className="bg-[#121722] border border-[#1e2638] border-solid flex min-w-[150px] max-w-[240px] gap-[8px] h-[32px] items-center px-[11px] py-px rounded-[6px]"
            >
              <img
                src="/icons/shell/lightning-emerald.svg"
                alt=""
                aria-hidden="true"
                className="h-[9px] w-[5px] shrink-0"
              />
              <span className="font-medium text-[#f0f4f8] text-[12px] leading-[16px] whitespace-nowrap truncate min-w-0">
                {currentWorkspaceName}
              </span>
              <img
                src={
                  workspaceMenuOpen
                    ? "/icons/shell/efef2.svg"
                    : "/icons/shell/d5590.svg"
                }
                alt=""
                aria-hidden="true"
                className={cn(
                  "ml-auto size-[12px] shrink-0 transition-transform duration-150",
                  workspaceMenuOpen && "rotate-180",
                )}
              />
            </button>
            {renderWorkspaceDropdown()}
          </div>

          {/* Environment Badge */}
          <div
            data-testid="environment-badge"
            aria-label={`Deployment Environment: ${ENV_LABEL}`}
            className="hidden min-[769px]:flex bg-[rgba(16,185,129,0.1)] border border-[rgba(16,185,129,0.3)] border-solid gap-[6px] h-[28px] items-center px-[11px] py-px rounded-[6px]"
          >
            <span className="font-mono font-semibold text-[#10b981] text-[11px] leading-[16.5px] tracking-[0.275px] uppercase whitespace-nowrap">
              {ENV_LABEL}
            </span>
          </div>

          {/* Search Field */}
          <form
            role="search"
            onSubmit={handleProjectSearchSubmit}
            className="relative h-[32px] w-[140px] sm:w-[160px]"
          >
            <label htmlFor={tabletSearchInputId} className="sr-only">
              Search projects
            </label>
            <input
              id={tabletSearchInputId}
              type="text"
              value={projectSearch}
              onChange={(e) => setProjectSearch(e.target.value)}
              placeholder="Search projects..."
              className="bg-[#0A0E18] border border-[#1e2638] border-solid h-[32px] w-full rounded-[6px] pl-[32px] pr-[12px] py-[7px] text-[12px] leading-[16px] text-[#f0f4f8] placeholder:text-[#6b7280] focus:border-[#10b981] focus:outline-none"
            />
            <img
              src="/icons/shell/6dd21.svg"
              alt=""
              aria-hidden="true"
              className="pointer-events-none absolute left-[10px] top-1/2 -translate-y-1/2 size-[12px]"
            />
          </form>

          {/* Notification Bell */}
          <Link
            href="/risks-alerts"
            aria-label="View Risks and Alerts"
            data-testid="header-alerts-link"
            className="relative flex size-[32px] shrink-0 items-center justify-center rounded-[6px]"
          >
            <img
              src="/icons/shell/3a4fe.svg"
              alt=""
              aria-hidden="true"
              className="size-[32px]"
            />
          </Link>

          {/* User Profile & Sign Out */}
          <div ref={userMenuRef} className="relative flex items-center gap-[10px] pl-[4px]">
            <span data-testid="user-tenant-badge" className="sr-only">
              {currentWorkspaceName}
            </span>

            <button
              type="button"
              data-testid="user-profile-menu-trigger"
              onClick={() => setUserMenuOpen(!userMenuOpen)}
              aria-expanded={userMenuOpen}
              aria-haspopup="menu"
              aria-label={`User menu for ${effectiveDisplayName}`}
              className="flex items-center gap-[8px] rounded-[6px]"
            >
              <div className="bg-[#0A0E18] border border-[rgba(16,185,129,0.3)] border-solid flex size-[28px] items-center justify-center rounded-[6px] p-px">
                <span className="font-mono font-bold text-[#10b981] text-[11px] leading-[16.5px]">
                  {getInitials(effectiveDisplayName)}
                </span>
              </div>
              <span data-testid="user-profile-name" className="sr-only">
                {effectiveDisplayName}
              </span>
            </button>

            {renderUserDropdown()}
          </div>
        </div>
      </header>
    );
  }

  /* ------------------------------------------------------------------ */
  /* DESKTOP HEADER (Figma 325:2340, 321:1283, 334:5847 - 1-to-1)       */
  /* ------------------------------------------------------------------ */
  return (
    <header
      data-testid="portal-header"
      role="banner"
      aria-label="Application Header"
      className="bg-[#161b26] border-[#1f2433] border-b border-solid flex h-[48px] items-center justify-between pb-px px-[16px] shrink-0 w-full z-30"
    >
      {/* Left Zone: Mobile toggle & CONSOLE label (Figma 325:2823) */}
      <div className="flex items-center gap-[8px]">
        <button
          type="button"
          onClick={onMobileMenuToggle}
          aria-label="Open mobile navigation"
          data-testid="sidebar-mobile-toggle"
          className="flex h-[28px] w-[28px] items-center justify-center rounded-[6px] border border-[#1f2433] bg-[#0f131d] text-[#e2e8f0] md:hidden"
        >
          <Menu className="h-4 w-4" />
        </button>

        <nav aria-label="Header Breadcrumb" className="flex items-center">
          <div className="flex items-center px-[6px] py-[2px] rounded-[4px]">
            <span className="font-mono text-[#7c839b] text-[10px] leading-[15px] tracking-[0.8px] uppercase">
              Console
            </span>
          </div>
        </nav>
      </div>

      {/* Right Zone: Controls Row (Figma 325:2827) */}
      <div className="flex gap-[10px] items-center">
        {/* 1. Workspace Selector Dropdown (Figma 325:2828 & 334:6457) */}
        <div ref={workspaceRef} className="relative">
          <button
            type="button"
            data-testid="workspace-selector-trigger"
            onClick={() => setWorkspaceMenuOpen(!workspaceMenuOpen)}
            aria-expanded={workspaceMenuOpen}
            aria-haspopup="menu"
            aria-label={`Current Workspace: ${currentWorkspaceName}. Click to switch.`}
            className={cn(
              "flex min-w-[150px] max-w-[240px] h-[32px] shrink-0 gap-[8px] items-center px-[11px] rounded-[6px] border border-solid transition-all focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
              workspaceMenuOpen
                ? "bg-[#171B26] border-[#00e599] shadow-[0px_0px_10px_0px_rgba(0,229,153,0.1)]"
                : "bg-[#171B26] border-[#252B3B] hover:border-[#7c839b]/40",
            )}
          >
            <img
              src="/icons/shell/lightning-emerald.svg"
              alt=""
              aria-hidden="true"
              className="h-[9px] w-[5px] shrink-0"
            />
            <span className="font-medium text-[#e2e8f0] text-[12px] leading-[16px] whitespace-nowrap truncate min-w-0">
              {currentWorkspaceName}
            </span>
            <img
              src={
                workspaceMenuOpen
                  ? "/icons/shell/efef2.svg"
                  : "/icons/shell/d5590.svg"
              }
              alt=""
              aria-hidden="true"
              className={cn(
                "ml-auto size-[12px] shrink-0 transition-transform duration-150",
                workspaceMenuOpen && "rotate-180",
              )}
            />
          </button>

          {renderWorkspaceDropdown()}
        </div>

        {/* 2. Environment Badge (Figma 325:2837) */}
        <div
          data-testid="environment-badge"
          aria-label={`Deployment Environment: ${ENV_LABEL}`}
          className="hidden sm:flex h-[28px] min-w-[96px] px-[10px] shrink-0 items-center justify-center bg-[rgba(0,229,153,0.1)] border border-[rgba(0,229,153,0.2)] border-solid rounded-[6px]"
        >
          <span className="font-mono text-[#00e599] text-[10px] leading-[15px] tracking-[0.5px] uppercase whitespace-nowrap">
            {ENV_LABEL}
          </span>
        </div>

        {/* 3. Global Search Field (Figma 325:2839) */}
        <form
          role="search"
          onSubmit={handleProjectSearchSubmit}
          className="relative hidden lg:flex bg-[#0a0e18] border border-[#252b3b] border-solid h-[32px] items-center px-[9px] py-px rounded-[6px] w-[192px] gap-[6px] focus-within:border-[#00e599] transition-colors"
        >
          <img
            src="/icons/shell/4738c.svg"
            alt=""
            aria-hidden="true"
            className="size-[12px] shrink-0"
          />
          <label htmlFor={desktopSearchInputId} className="sr-only">
            Search projects
          </label>
          <input
            id={desktopSearchInputId}
            type="text"
            value={projectSearch}
            onChange={(e) => setProjectSearch(e.target.value)}
            placeholder="Search projects..."
            className="w-full bg-transparent border-0 border-none outline-none focus:outline-none focus:ring-0 text-[#e2e8f0] placeholder:text-[#64748b] text-[11px] leading-normal p-0 m-0"
          />
        </form>

        {/* 4. Notification Bell Icon Button (Figma 325:2845) */}
        <Link
          href="/risks-alerts"
          aria-label="View Risks and Alerts"
          data-testid="header-alerts-link"
          className="bg-[#171b26] border border-[#252b3b] border-solid flex items-center justify-center rounded-[6px] relative shrink-0 size-[32px] hover:border-[#7c839b]/40 transition-colors"
        >
          <img
            src="/icons/shell/cea19.svg"
            alt=""
            aria-hidden="true"
            className="size-[14px]"
          />
        </Link>

        {/* 6. User Profile Trigger & Sign Out (Figma 325:2849 & 321:1972) */}
        {showProfile ? (
          <div ref={userMenuRef} className="relative flex gap-[6px] items-center">
            <span data-testid="user-tenant-badge" className="sr-only">
              {currentWorkspaceName}
            </span>

            <button
              type="button"
              data-testid="user-profile-menu-trigger"
              onClick={() => setUserMenuOpen(!userMenuOpen)}
              aria-expanded={userMenuOpen}
              aria-haspopup="menu"
              aria-label={`User menu for ${effectiveDisplayName}`}
              className={cn(
                "flex gap-[8px] items-center rounded-[6px] border border-solid px-[7px] py-[5px] bg-[#0A0E18]/10 transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00e599]",
                userMenuOpen
                  ? "border-[#00e599]"
                  : "border-[#252b3b] hover:border-[#7c839b]/40",
              )}
            >
              {/* Avatar (24x24) */}
              <div className="bg-[#0A0E18] border border-[rgba(0,229,153,0.3)] border-solid flex items-center justify-center p-px rounded-[6px] shrink-0 size-[24px]">
                <span className="font-bold text-[#00e599] text-[10px] leading-[15px] text-center">
                  {getInitials(effectiveDisplayName)}
                </span>
              </div>

              {/* Name + Role Badge */}
              <div className="hidden sm:flex items-center">
                <span
                  data-testid="user-profile-name"
                  className="font-medium text-[#e2e8f0] text-[12px] leading-[16px] whitespace-nowrap"
                >
                  {effectiveDisplayName}
                </span>
                <span className="hidden md:inline-flex pl-[6px]">
                  <span
                    data-testid="user-role-badge"
                    className="bg-[#0A0E18]/10 border border-[rgba(0,229,153,0.3)] border-solid inline-flex items-center px-[7px] py-[3px] rounded-[4px] font-mono text-[#00e599] text-[10px] leading-[10px] whitespace-nowrap"
                  >
                    {roleLabel}
                  </span>
                </span>
              </div>

              {/* Chevron */}
              <img
                src={
                  userMenuOpen
                    ? "/icons/shell/efef2.svg"
                    : "/icons/shell/d5590.svg"
                }
                alt=""
                aria-hidden="true"
                className={cn(
                  "size-[12px] shrink-0 transition-transform duration-150",
                  userMenuOpen && "rotate-180"
                )}
              />
            </button>

            {renderUserDropdown()}
          </div>
        ) : (
          <Link
            href="/login"
            data-testid="header-signin-button"
            className="bg-[#00e599] flex items-center justify-center px-[12px] py-[5px] rounded-[6px] font-semibold text-[#0f131d] text-[11px] leading-[16.5px] whitespace-nowrap"
          >
            Sign In
          </Link>
        )}
      </div>
    </header>
  );
}
