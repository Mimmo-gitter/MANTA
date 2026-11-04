import type { ActionId } from "@/lib/permissions";

export type NavSectionId = "WORKSPACE" | "ENGINEERING" | "ADMINISTRATION";

export interface NavItemConfig {
  id: string;
  label: string;
  tabletLabel: string;
  href: string;
  section: NavSectionId;
  icon: string;
  activeIcon: string;
  tabletCollapsedIcon: string;
  tabletCollapsedActiveIcon: string;
  tabletCollapsedWidth: number;
  tabletCollapsedHeight: number;
  tabletExpandedIcon: string;
  tabletExpandedActiveIcon: string;
  tabletExpandedWidth: number;
  tabletExpandedHeight: number;
  badge?: string;
  hasAlertDot?: boolean;
  description: string;
  requiredAction?: ActionId;
  requiredRole?: string;
}

export interface NavSectionConfig {
  id: NavSectionId;
  title: string;
  items: NavItemConfig[];
}

export const NAVIGATION_SECTIONS: NavSectionConfig[] = [
  {
    id: "WORKSPACE",
    title: "WORKSPACE",
    items: [
      {
        id: "overview",
        label: "Overview",
        tabletLabel: "Overview",
        href: "/",
        section: "WORKSPACE",
        icon: "/icons/shell/49043.svg",
        activeIcon: "/icons/shell/8da2e.svg",
        tabletCollapsedIcon: "/icons/shell/dca9b-inactive.svg",
        tabletCollapsedActiveIcon: "/icons/shell/dca9b.svg",
        tabletCollapsedWidth: 13.333,
        tabletCollapsedHeight: 15,
        tabletExpandedIcon: "/icons/shell/e177b-inactive.svg",
        tabletExpandedActiveIcon: "/icons/shell/e177b.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 15,
        description:
          "Workspace-level executive overview, active project telemetry, and real-time health indicators.",
      },
      {
        id: "projects",
        label: "Projects",
        tabletLabel: "Projects",
        href: "/projects",
        section: "WORKSPACE",
        icon: "/icons/shell/2bfb7.svg",
        activeIcon: "/icons/shell/2bfb7-active.svg",
        tabletCollapsedIcon: "/icons/shell/637b6.svg",
        tabletCollapsedActiveIcon: "/icons/shell/637b6-active.svg",
        tabletCollapsedWidth: 16.667,
        tabletCollapsedHeight: 13.333,
        tabletExpandedIcon: "/icons/shell/78357.svg",
        tabletExpandedActiveIcon: "/icons/shell/78357-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 13.333,
        badge: "12",
        description:
          "Directory of connected engineering repositories, delivery streams, and project workspaces.",
      },
      {
        id: "delivery-analytics",
        label: "Delivery Analytics",
        tabletLabel: "Delivery Analytics",
        href: "/delivery-analytics",
        section: "WORKSPACE",
        icon: "/icons/shell/71ec7.svg",
        activeIcon: "/icons/shell/71ec7-active.svg",
        tabletCollapsedIcon: "/icons/shell/bbd70.svg",
        tabletCollapsedActiveIcon: "/icons/shell/bbd70-active.svg",
        tabletCollapsedWidth: 13.333,
        tabletCollapsedHeight: 13.333,
        tabletExpandedIcon: "/icons/shell/64495.svg",
        tabletExpandedActiveIcon: "/icons/shell/64495-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 13.333,
        description:
          "Throughput, cycle time, DORA metrics, and cross-project delivery velocity trends.",
      },
      {
        id: "risks-alerts",
        label: "Risks & Alerts",
        tabletLabel: "Risks & Alerts",
        href: "/risks-alerts",
        section: "WORKSPACE",
        icon: "/icons/shell/81919.svg",
        activeIcon: "/icons/shell/81919-active.svg",
        tabletCollapsedIcon: "/icons/shell/5b1ba.svg",
        tabletCollapsedActiveIcon: "/icons/shell/5b1ba-active.svg",
        tabletCollapsedWidth: 18.333,
        tabletCollapsedHeight: 15.833,
        tabletExpandedIcon: "/icons/shell/02f24.svg",
        tabletExpandedActiveIcon: "/icons/shell/02f24-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 15.833,
        hasAlertDot: true,
        description:
          "Early-warning signals, delivery bottlenecks, and automated SLA risk notifications.",
      },
    ],
  },
  {
    id: "ENGINEERING",
    title: "ENGINEERING",
    items: [
      {
        id: "sprint-wip",
        label: "Sprint & WIP Monitoring",
        tabletLabel: "Sprint & WIP",
        href: "/sprint-wip",
        section: "ENGINEERING",
        icon: "/icons/shell/49e13.svg",
        activeIcon: "/icons/shell/49e13-active.svg",
        tabletCollapsedIcon: "/icons/shell/ee2cf.svg",
        tabletCollapsedActiveIcon: "/icons/shell/ee2cf-active.svg",
        tabletCollapsedWidth: 13.333,
        tabletCollapsedHeight: 16.667,
        tabletExpandedIcon: "/icons/shell/6e856.svg",
        tabletExpandedActiveIcon: "/icons/shell/6e856-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 16.667,
        description:
          "Real-time sprint burndown, work-in-progress limits, and blocked ticket diagnostics.",
      },
      {
        id: "scope-creep",
        label: "Scope Creep",
        tabletLabel: "Scope Creep",
        href: "/scope-creep",
        section: "ENGINEERING",
        icon: "/icons/shell/fb356.svg",
        activeIcon: "/icons/shell/fb356-active.svg",
        tabletCollapsedIcon: "/icons/shell/e35f3.svg",
        tabletCollapsedActiveIcon: "/icons/shell/e35f3-active.svg",
        tabletCollapsedWidth: 16.667,
        tabletCollapsedHeight: 10,
        tabletExpandedIcon: "/icons/shell/1d619.svg",
        tabletExpandedActiveIcon: "/icons/shell/1d619-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 10,
        description:
          "Detection and analysis of unscheduled scope expansion across active iterations.",
      },
      {
        id: "adr-knowledge-base",
        label: "ADR Knowledge Base",
        tabletLabel: "ADR Knowledge",
        href: "/adr-knowledge-base",
        section: "ENGINEERING",
        icon: "/icons/shell/be1f8.svg",
        activeIcon: "/icons/shell/be1f8-active.svg",
        tabletCollapsedIcon: "/icons/shell/3e0eb.svg",
        tabletCollapsedActiveIcon: "/icons/shell/3e0eb-active.svg",
        tabletCollapsedWidth: 18.333,
        tabletCollapsedHeight: 13.333,
        tabletExpandedIcon: "/icons/shell/90ccb.svg",
        tabletExpandedActiveIcon: "/icons/shell/90ccb-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 13.333,
        description:
          "Architecture Decision Records repository, technical context graph, and decision history.",
      },
      {
        id: "semantic-search",
        label: "Semantic Search",
        tabletLabel: "Semantic Search",
        href: "/semantic-search",
        section: "ENGINEERING",
        icon: "/icons/shell/2b553.svg",
        activeIcon: "/icons/shell/2b553-active.svg",
        tabletCollapsedIcon: "/icons/shell/f780f.svg",
        tabletCollapsedActiveIcon: "/icons/shell/f780f-active.svg",
        tabletCollapsedWidth: 15,
        tabletCollapsedHeight: 15,
        tabletExpandedIcon: "/icons/shell/57339.svg",
        tabletExpandedActiveIcon: "/icons/shell/57339-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 15,
        description:
          "AI-powered vector search across codebases, pull requests, ADRs, and issue trackers.",
      },
      {
        id: "refactoring-verification",
        label: "Refactoring & Verification",
        tabletLabel: "Refactoring",
        href: "/refactoring-verification",
        section: "ENGINEERING",
        icon: "/icons/shell/00a87.svg",
        activeIcon: "/icons/shell/00a87-active.svg",
        tabletCollapsedIcon: "/icons/shell/7e99d.svg",
        tabletCollapsedActiveIcon: "/icons/shell/7e99d-active.svg",
        tabletCollapsedWidth: 13.333,
        tabletCollapsedHeight: 16.667,
        tabletExpandedIcon: "/icons/shell/eab2a.svg",
        tabletExpandedActiveIcon: "/icons/shell/eab2a-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 16.667,
        description:
          "Automated refactoring impact analysis, architectural drift checks, and verification gates.",
      },
    ],
  },
  {
    id: "ADMINISTRATION",
    title: "ADMINISTRATION",
    items: [
      {
        id: "workspace-settings",
        label: "Workspace Settings",
        tabletLabel: "Workspace Settings",
        href: "/workspace-settings",
        section: "ADMINISTRATION",
        requiredAction: "MANAGE_TENANT_USERS_AND_ROLES",
        requiredRole: "TENANT_ADMIN",
        icon: "/icons/shell/e0670.svg",
        activeIcon: "/icons/shell/e0670-active.svg",
        tabletCollapsedIcon: "/icons/shell/aed70.svg",
        tabletCollapsedActiveIcon: "/icons/shell/aed70-active.svg",
        tabletCollapsedWidth: 16.75,
        tabletCollapsedHeight: 16.667,
        tabletExpandedIcon: "/icons/shell/78866.svg",
        tabletExpandedActiveIcon: "/icons/shell/78866-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 16.667,
        description:
          "Global tenant preferences, regional data residency, and workspace configuration.",
      },
      {
        id: "integrations",
        label: "Integrations",
        tabletLabel: "Integrations",
        href: "/integrations",
        section: "ADMINISTRATION",
        requiredAction: "MANAGE_WEBHOOK_INTEGRATIONS",
        requiredRole: "TENANT_ADMIN",
        icon: "/icons/shell/9a85f.svg",
        activeIcon: "/icons/shell/9a85f-active.svg",
        tabletCollapsedIcon: "/icons/shell/7a396.svg",
        tabletCollapsedActiveIcon: "/icons/shell/7a396-active.svg",
        tabletCollapsedWidth: 15.833,
        tabletCollapsedHeight: 16.667,
        tabletExpandedIcon: "/icons/shell/d9ae7.svg",
        tabletExpandedActiveIcon: "/icons/shell/d9ae7-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 16.667,
        description:
          "Configure and monitor GitHub, GitLab, Jira, Linear, and CI/CD webhook connectors.",
      },
      {
        id: "access-roles",
        label: "Access & Roles",
        tabletLabel: "Access & Roles",
        href: "/access-roles",
        section: "ADMINISTRATION",
        requiredAction: "MANAGE_TENANT_USERS_AND_ROLES",
        requiredRole: "TENANT_ADMIN",
        icon: "/icons/shell/39c1f.svg",
        activeIcon: "/icons/shell/39c1f-active.svg",
        tabletCollapsedIcon: "/icons/shell/6d0cc.svg",
        tabletCollapsedActiveIcon: "/icons/shell/6d0cc-active.svg",
        tabletCollapsedWidth: 18.333,
        tabletCollapsedHeight: 13.333,
        tabletExpandedIcon: "/icons/shell/2f0e3.svg",
        tabletExpandedActiveIcon: "/icons/shell/2f0e3-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 13.333,
        description:
          "Role-based access control (RBAC), Keycloak identity mappings, and tenant permissions.",
      },
      {
        id: "privacy-erasure",
        label: "Privacy & Erasure",
        tabletLabel: "Privacy & Erasure",
        href: "/privacy-erasure",
        section: "ADMINISTRATION",
        requiredAction: "MANAGE_TENANT_USERS_AND_ROLES",
        requiredRole: "TENANT_ADMIN",
        icon: "/icons/shell/597b1.svg",
        activeIcon: "/icons/shell/597b1-active.svg",
        tabletCollapsedIcon: "/icons/shell/c1f3b.svg",
        tabletCollapsedActiveIcon: "/icons/shell/c1f3b-active.svg",
        tabletCollapsedWidth: 13.333,
        tabletCollapsedHeight: 15,
        tabletExpandedIcon: "/icons/shell/18eb6.svg",
        tabletExpandedActiveIcon: "/icons/shell/18eb6-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 15,
        description:
          "Crypto-shredding, GDPR right-to-erasure workflows, and PII redaction audit logs.",
      },
      {
        id: "adr-governance",
        label: "ADR Governance",
        tabletLabel: "ADR Governance",
        href: "/adr-governance",
        section: "ADMINISTRATION",
        requiredAction: "PUBLISH_GLOBAL_ADR",
        requiredRole: "TENANT_ADMIN",
        icon: "/icons/shell/00a87.svg",
        activeIcon: "/icons/shell/00a87-active.svg",
        tabletCollapsedIcon: "/icons/shell/7167b.svg",
        tabletCollapsedActiveIcon: "/icons/shell/7167b-active.svg",
        tabletCollapsedWidth: 18.333,
        tabletCollapsedHeight: 17.5,
        tabletExpandedIcon: "/icons/shell/97234.svg",
        tabletExpandedActiveIcon: "/icons/shell/97234-active.svg",
        tabletExpandedWidth: 24,
        tabletExpandedHeight: 17.5,
        description:
          "Compliance rules, approval workflows, and mandatory sign-off policies for architectural decisions.",
      },
    ],
  },
];

export const sidebarSections = NAVIGATION_SECTIONS;

export const ALL_NAV_ITEMS: NavItemConfig[] = NAVIGATION_SECTIONS.flatMap(
  (section) => section.items,
);

export function getNavItemByPathname(pathname: string): NavItemConfig | undefined {
  return ALL_NAV_ITEMS.find((item) =>
    item.href === "/"
      ? pathname === "/"
      : pathname === item.href || pathname.startsWith(`${item.href}/`),
  );
}
