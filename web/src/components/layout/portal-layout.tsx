"use client";

import { useState, useEffect } from "react";
import { Sidebar } from "@/components/layout/sidebar";
import { PortalHeader } from "@/components/layout/portal-header";
import { ShellStateProvider } from "@/components/layout/shell-context";
import { cn } from "@/lib/utils";

interface PortalLayoutProps {
  children: React.ReactNode;
}

export function PortalLayout({ children }: PortalLayoutProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [isTablet, setIsTablet] = useState(false);
  const [tabletCollapsed, setTabletCollapsed] = useState(false);

  // Detect tablet viewports (768px - 1023px) and default to collapsed (72px) on tablet (Figma 338:12871)
  useEffect(() => {
    function handleResize() {
      const width = window.innerWidth;
      if (width >= 768 && width <= 1024) {
        setIsTablet(true);
        setTabletCollapsed(true);
      } else {
        setIsTablet(false);
        setTabletCollapsed(false);
      }
    }

    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  return (
    <ShellStateProvider>
      <div
        className={cn(
          "flex h-screen w-full overflow-hidden",
          isTablet
            ? "bg-[#0a0d14] text-[#f0f4f8]"
            : "bg-[#0f131d] text-[#e2e8f0]"
        )}
      >
        {/* Accessibility: Skip to Main Content Link */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[100] focus:rounded-[6px] focus:bg-[#00e599] focus:px-4 focus:py-2 focus:text-xs focus:font-bold focus:text-[#0f131d] focus:shadow-lg focus:outline-none"
        >
          Skip to main content
        </a>

        {/* Left Persistent Sidebar (Desktop 216px, Tablet 72px/240px, Mobile drawer) */}
        <Sidebar
          mobileOpen={mobileOpen}
          onMobileClose={() => setMobileOpen(false)}
          collapsed={tabletCollapsed}
          isTablet={isTablet}
          onToggleCollapse={() => setTabletCollapsed((prev) => !prev)}
        />

        {/* Main Area: Top Bar + Page Content */}
        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          <PortalHeader
            onMobileMenuToggle={() => setMobileOpen((prev) => !prev)}
            isTabletCollapsed={tabletCollapsed}
            isTablet={isTablet}
          />

          {/* Main Workspace Canvas (Figma 325:2861: bg-[#0f131d], Tablet 338:12871: bg-[#0a0d14]) */}
          <main
            id="main-content"
            tabIndex={-1}
            role="main"
            className={cn(
              "relative flex-1 overflow-y-auto outline-none focus:outline-none",
              isTablet ? "bg-[#0a0d14]" : "bg-[#0f131d]"
            )}
          >
            {children}
          </main>
        </div>
      </div>
    </ShellStateProvider>
  );
}
