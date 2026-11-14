"use client";

import { useEffect, useId, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  Copy,
  ExternalLink,
  GitBranch,
  Link2,
  Plus,
  Search,
  Unlink2,
} from "lucide-react";
import { getIntegrations } from "@/lib/integrations-api";
import { usePermissions } from "@/hooks/use-permissions";
import { getPrimaryRoleLabel } from "@/lib/permissions";
import type { Integration } from "@/types/integration";
import type { ProjectItem, RepositoryItem } from "@/types/project";
import { LinkRepositoriesModal } from "@/components/projects/link-repositories-modal";
import { UnlinkRepositoryModal } from "@/components/projects/unlink-repository-modal";

interface LinkedRepositoriesTabProps {
  project: ProjectItem;
  linkModalOpen: boolean;
  onOpenLinkModal: () => void;
  onCloseLinkModal: () => void;
}

const PAGE_SIZE = 5;

function getRepoInitials(fullName: string): string {
  const parts = fullName.split("/");
  const target = parts[parts.length - 1] || fullName;
  const words = target.split(/[-_.]+/).filter(Boolean);
  if (words.length >= 2) {
    return `${words[0][0]}${words[1][0]}`.toUpperCase();
  }
  return target.slice(0, 2).toUpperCase();
}

export function LinkedRepositoriesTab({
  project,
  linkModalOpen,
  onOpenLinkModal,
  onCloseLinkModal,
}: LinkedRepositoriesTabProps) {
  const { can, roles } = usePermissions();
  const isTenantAdmin = can("MANAGE_WEBHOOK_INTEGRATIONS");
  const roleLabel = getPrimaryRoleLabel(roles);
  const searchInputId = useId();

  const [repoSearch, setRepoSearch] = useState("");
  const [orgMenuOpen, setOrgMenuOpen] = useState(false);
  const [selectedIntegrationId, setSelectedIntegrationId] = useState("");
  const [page, setPage] = useState(1);
  const [unlinkTarget, setUnlinkTarget] = useState<RepositoryItem | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const integrationsQuery = useQuery<Integration[]>({
    queryKey: ["integrations"],
    queryFn: getIntegrations,
    enabled: isTenantAdmin,
    retry: 1,
    staleTime: 30_000,
  });

  const githubIntegrations = useMemo(
    () =>
      (integrationsQuery.data ?? []).filter(
        (integration) => integration.provider === "github",
      ),
    [integrationsQuery.data],
  );

  const activeGitHubIntegrations = useMemo(
    () => githubIntegrations.filter((item) => item.is_active),
    [githubIntegrations],
  );

  const selectedIntegration = useMemo(() => {
    if (selectedIntegrationId) {
      return githubIntegrations.find((i) => i.id === selectedIntegrationId);
    }
    return activeGitHubIntegrations[0] || githubIntegrations[0];
  }, [activeGitHubIntegrations, githubIntegrations, selectedIntegrationId]);

  useEffect(() => {
    if (!toastMessage) return;
    const timer = window.setTimeout(() => setToastMessage(null), 5000);
    return () => window.clearTimeout(timer);
  }, [toastMessage]);

  const filteredRepos = useMemo(() => {
    const query = repoSearch.trim().toLowerCase();
    return project.repositories.filter((repo) => {
      const matchesQuery =
        !query ||
        repo.full_name.toLowerCase().includes(query) ||
        repo.default_branch.toLowerCase().includes(query);
      const matchesOrg =
        !selectedIntegrationId ||
        repo.integration_id === null ||
        repo.integration_id === selectedIntegrationId;
      return matchesQuery && matchesOrg;
    });
  }, [project.repositories, repoSearch, selectedIntegrationId]);

  const totalPages = Math.max(1, Math.ceil(filteredRepos.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const paginatedRepos = filteredRepos.slice(
    (safePage - 1) * PAGE_SIZE,
    safePage * PAGE_SIZE,
  );

  const showNoGitHubState =
    isTenantAdmin &&
    !integrationsQuery.isLoading &&
    !integrationsQuery.isError &&
    activeGitHubIntegrations.length === 0 &&
    project.repositories.length === 0;

  const activeOrgLabel = selectedIntegration
    ? `GitHub: ${
        selectedIntegration.external_account_id ||
        selectedIntegration.id.slice(0, 8)
      }`
    : isTenantAdmin && activeGitHubIntegrations.length === 0
      ? "GitHub: Not connected"
      : "GitHub App";

  const copyRepoName = async (fullName: string) => {
    try {
      await navigator.clipboard.writeText(`https://github.com/${fullName}`);
      setToastMessage(`Copied GitHub URL for ${fullName}`);
    } catch {
      setToastMessage(`Repository: https://github.com/${fullName}`);
    }
  };

  return (
    <div className="space-y-[16px]">
      {!isTenantAdmin && (
        <div
          role="status"
          data-testid="repos-readonly-banner"
          className="flex flex-wrap items-center gap-[10px] rounded-[10px] border border-[#f59e0b]/40 bg-[#f59e0b]/10 px-[14px] py-[10px] text-[12px] text-[#fcd34d]"
        >
          <span className="rounded-[4px] border border-[#f59e0b]/50 bg-[#f59e0b]/15 px-[7px] py-[2px] font-mono text-[10px] font-semibold uppercase tracking-[0.5px] text-[#fbbf24]">
            Read-Only
          </span>
          <span className="rounded-[4px] border border-[#334155] bg-[#0f131d] px-[7px] py-[2px] font-mono text-[10px] uppercase text-[#cbd5e1]">
            Role: {roleLabel}
          </span>
          <span className="text-[#fde68a]">
            Only Tenant Administrators can link or unlink GitHub repositories.
          </span>
        </div>
      )}

      {/* Status Summary Bar (Figma 3A / 3F) */}
      <div className="flex flex-wrap items-center gap-x-[18px] gap-y-[6px] rounded-[10px] border border-[#1e293b] bg-[#0d121f] px-[16px] py-[11px] font-mono text-[12px]">
        <span className="font-medium text-[#f8fafc]">
          Repositories: {project.repositories.length} linked
        </span>
        <span className="text-[#475569]" aria-hidden="true">
          ·
        </span>
        {isTenantAdmin && integrationsQuery.isError ? (
          <span className="text-[#f87171]">
            Could not verify GitHub integrations (you can still paste a repo URL)
          </span>
        ) : showNoGitHubState ? (
          <span className="text-[#f59e0b]">GitHub: Not connected</span>
        ) : (
          <span className="text-[#00e599]">
            GitHub App:{" "}
            {selectedIntegration?.external_account_id || "Connected"}
          </span>
        )}
        <span className="text-[#475569]" aria-hidden="true">
          ·
        </span>
        <span className="text-[#94a3b8]">
          {showNoGitHubState
            ? "Setup required"
            : `${project.repositories.filter((r) => r.is_private).length} private · ${project.repositories.filter((r) => !r.is_private).length} public`}
        </span>
      </div>

      {/* Main Card */}
      <div className="rounded-[12px] border border-[#1e293b] bg-[#0d121f]">
        {/* Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-[12px] border-b border-[#1e293b] px-[16px] py-[12px]">
          <div className="flex items-center gap-[8px]">
            <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.8px] text-[#94a3b8]">
              Linked Repositories
            </h2>
            <span className="rounded-[4px] border border-[#00e599]/30 bg-[#00e599]/10 px-[6px] py-[1px] font-mono text-[11px] font-semibold text-[#00e599]">
              {filteredRepos.length}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-[8px]">
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-[10px] top-1/2 size-[13px] -translate-y-1/2 text-[#64748b]"
                aria-hidden="true"
              />
              <label htmlFor={searchInputId} className="sr-only">
                Filter linked repositories
              </label>
              <input
                id={searchInputId}
                type="text"
                value={repoSearch}
                onChange={(e) => {
                  setRepoSearch(e.target.value);
                  setPage(1);
                }}
                placeholder="Filter linked repositories..."
                className="h-[32px] w-[210px] rounded-[6px] border border-[#252b3b] bg-[#0a0e18] pl-[30px] pr-[10px] text-[12px] text-[#e2e8f0] placeholder:text-[#64748b] focus:border-[#00e599] focus:outline-none"
                data-testid="filter-linked-repos-input"
              />
            </div>

            {isTenantAdmin && (
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setOrgMenuOpen((prev) => !prev)}
                  aria-expanded={orgMenuOpen}
                  aria-haspopup="menu"
                  className="inline-flex h-[32px] items-center gap-2 rounded-[6px] border border-[#252b3b] bg-[#0a0e18] px-[10px] font-mono text-[11px] text-[#cbd5e1] transition-colors hover:border-[#3b455e]"
                  data-testid="github-org-filter-trigger"
                >
                  <span>{activeOrgLabel}</span>
                  <ChevronDown className="size-[12px]" aria-hidden="true" />
                </button>
                {orgMenuOpen && (
                  <div
                    role="menu"
                    className="absolute right-0 top-[calc(100%+6px)] z-30 w-[260px] rounded-[10px] border border-[#252b3b] bg-[#131a26] p-[6px] shadow-xl"
                  >
                    <div className="px-[8px] py-[4px] font-mono text-[10px] uppercase tracking-[0.6px] text-[#64748b]">
                      GitHub Integration (Org)
                    </div>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setSelectedIntegrationId("");
                        setOrgMenuOpen(false);
                        setPage(1);
                      }}
                      className={`mt-[2px] flex w-full items-center justify-between rounded-[6px] px-[8px] py-[6px] text-left font-mono text-[11px] ${
                        !selectedIntegrationId
                          ? "bg-[#00e599]/10 text-[#00e599]"
                          : "text-[#cbd5e1] hover:bg-white/[0.04]"
                      }`}
                    >
                      <span>All GitHub integrations</span>
                      <span>{project.repositories.length}</span>
                    </button>
                    {githubIntegrations.map((integration) => (
                      <button
                        key={integration.id}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setSelectedIntegrationId(integration.id);
                          setOrgMenuOpen(false);
                          setPage(1);
                        }}
                        className={`mt-[2px] flex w-full items-center justify-between rounded-[6px] px-[8px] py-[6px] text-left font-mono text-[11px] ${
                          selectedIntegrationId === integration.id
                            ? "bg-[#00e599]/10 text-[#00e599]"
                            : "text-[#cbd5e1] hover:bg-white/[0.04]"
                        }`}
                      >
                        <span className="truncate">
                          {integration.external_account_id ||
                            `GitHub (${integration.id.slice(0, 8)})`}
                        </span>
                        <span className="text-[10px] text-[#64748b]">
                          {integration.is_active ? "Active" : "Inactive"}
                        </span>
                      </button>
                    ))}
                    <div className="mt-[6px] border-t border-[#1e293b] pt-[6px]">
                      <Link
                        href="/integrations"
                        onClick={() => setOrgMenuOpen(false)}
                        className="flex items-center justify-between rounded-[6px] px-[8px] py-[6px] font-mono text-[11px] text-[#f59e0b] hover:bg-white/[0.04]"
                      >
                        <span>Manage GitHub Integrations</span>
                        <ArrowRight className="size-[12px]" aria-hidden="true" />
                      </Link>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Body */}
        {showNoGitHubState ? (
          <div className="p-[24px]">
            <div className="flex flex-col items-center justify-center rounded-[12px] border border-dashed border-[#252b3b] bg-[#0a0e18] px-[24px] py-[44px] text-center">
              <div className="flex size-[44px] items-center justify-center rounded-full border border-[#00e599]/30 bg-[#00e599]/10 text-[#00e599]">
                <Link2 className="size-[20px]" aria-hidden="true" />
              </div>
              <h3 className="mt-[14px] text-[16px] font-semibold text-[#f8fafc]">
                No GitHub integration connected
              </h3>
              <p className="mt-[6px] max-w-[460px] text-[13px] leading-[20px] text-[#94a3b8]">
                To link repositories to {project.name}, first connect your GitHub
                organization in Workspace Integrations.
              </p>
              {isTenantAdmin ? (
                <Link
                  href="/integrations"
                  data-testid="connect-github-integration-cta"
                  className="mt-[18px] inline-flex h-[36px] items-center gap-1.5 rounded-[8px] bg-[#00e599] px-[16px] text-[13px] font-semibold text-[#07111a] transition-colors hover:bg-[#1ef5ab]"
                >
                  <span>Connect GitHub Integration</span>
                  <ArrowRight className="size-[14px]" aria-hidden="true" />
                </Link>
              ) : (
                <button
                  type="button"
                  disabled
                  aria-disabled="true"
                  title="Only Tenant Administrators can manage workspace integrations"
                  className="mt-[18px] inline-flex h-[36px] cursor-not-allowed items-center gap-1.5 rounded-[8px] bg-[#00e599]/40 px-[16px] text-[13px] font-semibold text-[#07111a] opacity-60"
                >
                  <span>Connect GitHub Integration</span>
                  <ArrowRight className="size-[14px]" aria-hidden="true" />
                </button>
              )}
            </div>
          </div>
        ) : project.repositories.length === 0 ? (
          <div className="p-[24px]">
            <div className="flex flex-col items-center justify-center rounded-[12px] border border-dashed border-[#252b3b] bg-[#0a0e18] px-[24px] py-[40px] text-center">
              <div className="flex size-[42px] items-center justify-center rounded-full border border-[#00e599]/30 bg-[#00e599]/10 text-[#00e599]">
                <GitBranch className="size-[18px]" aria-hidden="true" />
              </div>
              <h3 className="mt-[14px] text-[15px] font-semibold text-[#f8fafc]">
                No repositories linked to {project.name} yet
              </h3>
              <p className="mt-[6px] max-w-[440px] text-[12px] leading-[19px] text-[#94a3b8]">
                Link GitHub repositories to enable pull request cycle time, commit
                attribution, and deployment event ingestion.
              </p>
              <button
                type="button"
                onClick={onOpenLinkModal}
                disabled={!isTenantAdmin}
                aria-disabled={!isTenantAdmin}
                title={
                  !isTenantAdmin
                    ? "Only Tenant Administrators can link repositories"
                    : undefined
                }
                data-testid="empty-link-repositories-button"
                className="mt-[16px] inline-flex h-[36px] items-center gap-1.5 rounded-[8px] bg-[#00e599] px-[16px] text-[12px] font-semibold text-[#07111a] transition-colors hover:bg-[#1ef5ab] disabled:cursor-not-allowed disabled:opacity-45"
              >
                <Plus className="size-[14px]" aria-hidden="true" />
                <span>Link Repositories</span>
              </button>
            </div>
          </div>
        ) : filteredRepos.length === 0 ? (
          <div className="flex flex-col items-center justify-center px-[20px] py-[38px] text-center">
            <AlertCircle
              className="size-[20px] text-[#64748b]"
              aria-hidden="true"
            />
            <p className="mt-[8px] text-[13px] font-medium text-[#e2e8f0]">
              No repositories match &ldquo;{repoSearch}&rdquo;
            </p>
            <button
              type="button"
              onClick={() => {
                setRepoSearch("");
                setSelectedIntegrationId("");
              }}
              className="mt-[10px] rounded-[6px] border border-[#252b3b] bg-[#0a0e18] px-[12px] py-[5px] text-[11px] font-medium text-[#cbd5e1] hover:border-[#3b455e]"
            >
              Reset filters
            </button>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left">
                <caption className="sr-only">
                  Linked GitHub repositories for {project.name}
                </caption>
                <thead>
                  <tr className="border-b border-[#1e293b] font-mono text-[10px] font-semibold uppercase tracking-[0.7px] text-[#64748b]">
                    <th scope="col" className="px-[16px] py-[10px]">
                      Repository (GitHub)
                    </th>
                    <th scope="col" className="px-[16px] py-[10px]">
                      Default Branch &amp; Visibility
                    </th>
                    <th scope="col" className="px-[16px] py-[10px]">
                      Link Status
                    </th>
                    <th scope="col" className="px-[16px] py-[10px] text-right">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#1e293b]/80">
                  {paginatedRepos.map((repo, idx) => (
                    <tr
                      key={repo.id}
                      data-testid={`linked-repo-row-${repo.full_name}`}
                      className="transition-colors hover:bg-white/[0.02]"
                    >
                      <td className="px-[16px] py-[12px]">
                        <div className="flex items-center gap-[12px]">
                          <div className="flex size-[32px] shrink-0 items-center justify-center rounded-full border border-[#1e3a5f] bg-[#10203a] font-mono text-[11px] font-semibold text-[#60a5fa]">
                            {getRepoInitials(repo.full_name)}
                          </div>
                          <div className="min-w-0">
                            <div className="flex items-center gap-[8px]">
                              <span className="truncate font-mono text-[13px] font-medium text-[#f8fafc]">
                                {repo.full_name}
                              </span>
                              {safePage === 1 && idx === 0 && (
                                <span className="rounded-[4px] border border-[#00e599]/30 bg-[#00e599]/10 px-[6px] py-[1px] font-mono text-[9px] font-semibold uppercase tracking-[0.5px] text-[#00e599]">
                                  Primary
                                </span>
                              )}
                            </div>
                            <a
                              href={`https://github.com/${repo.full_name}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 font-mono text-[11px] text-[#64748b] hover:text-[#94a3b8]"
                            >
                              <span>github.com/{repo.full_name}</span>
                              <ExternalLink
                                className="size-[10px]"
                                aria-hidden="true"
                              />
                            </a>
                          </div>
                        </div>
                      </td>

                      <td className="px-[16px] py-[12px]">
                        <span className="inline-flex items-center gap-1.5 rounded-[5px] border border-[#1e3a5f] bg-[#0a1628] px-[8px] py-[3px] font-mono text-[11px] text-[#93c5fd]">
                          <span
                            className="size-[5px] rounded-full bg-[#60a5fa]"
                            aria-hidden="true"
                          />
                          <span>
                            {repo.default_branch} ·{" "}
                            {repo.is_private ? "Private" : "Public"}
                          </span>
                        </span>
                      </td>

                      <td className="px-[16px] py-[12px]">
                        <span className="inline-flex items-center gap-1.5 rounded-[5px] border border-[#00e599]/30 bg-[#00e599]/10 px-[8px] py-[3px] font-mono text-[11px] text-[#00e599]">
                          <span
                            className="size-[5px] rounded-full bg-[#00e599]"
                            aria-hidden="true"
                          />
                          <span>Linked</span>
                        </span>
                      </td>

                      <td className="px-[16px] py-[12px]">
                        <div className="flex items-center justify-end gap-[6px]">
                          <button
                            type="button"
                            onClick={() => copyRepoName(repo.full_name)}
                            aria-label={`Copy GitHub URL for ${repo.full_name}`}
                            title="Copy repository URL"
                            className="inline-flex h-[28px] items-center justify-center rounded-[6px] border border-[#252b3b] bg-[#0a0e18] px-[8px] text-[#94a3b8] transition-colors hover:border-[#3b455e] hover:text-[#f8fafc]"
                          >
                            <Copy className="size-[12px]" aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setUnlinkTarget(repo)}
                            disabled={!isTenantAdmin}
                            aria-disabled={!isTenantAdmin}
                            title={
                              !isTenantAdmin
                                ? "Only Tenant Administrators can unlink repositories"
                                : `Unlink ${repo.full_name}`
                            }
                            data-testid={`unlink-repo-button-${repo.full_name}`}
                            className="inline-flex h-[28px] items-center gap-1 rounded-[6px] border border-[#252b3b] bg-[#0a0e18] px-[10px] text-[11px] font-medium text-[#e2e8f0] transition-colors hover:border-[#ef4444]/50 hover:text-[#fca5a5] disabled:cursor-not-allowed disabled:opacity-45"
                          >
                            <Unlink2 className="size-[12px]" aria-hidden="true" />
                            <span>Unlink</span>
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination Footer */}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[#1e293b] px-[16px] py-[10px] text-[11px] text-[#64748b]">
              <span>
                Showing {(safePage - 1) * PAGE_SIZE + 1}-
                {Math.min(safePage * PAGE_SIZE, filteredRepos.length)} of{" "}
                {filteredRepos.length} linked repositories
              </span>

              {totalPages > 1 && (
                <div className="flex items-center gap-[4px]">
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={safePage <= 1}
                    className="h-[26px] rounded-[5px] border border-[#252b3b] bg-[#0a0e18] px-[8px] font-mono text-[11px] text-[#94a3b8] hover:border-[#3b455e] disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    Prev
                  </button>
                  <span className="inline-flex h-[26px] min-w-[26px] items-center justify-center rounded-[5px] border border-[#00e599]/40 bg-[#00e599]/10 px-[6px] font-mono text-[11px] text-[#00e599]">
                    {safePage}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={safePage >= totalPages}
                    className="h-[26px] rounded-[5px] border border-[#252b3b] bg-[#0a0e18] px-[8px] font-mono text-[11px] text-[#94a3b8] hover:border-[#3b455e] disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    Next
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* Live Region Toast (Figma 3C) */}
      <div aria-live="polite" className="min-h-[1px]">
        {toastMessage && (
          <div
            role="status"
            data-testid="linked-repos-toast"
            className="flex items-center justify-between gap-3 rounded-[10px] border border-[#00e599]/35 bg-[#0e1a24] px-[14px] py-[10px] text-[12px] text-[#e2e8f0]"
          >
            <div className="flex items-center gap-2">
              <CheckCircle2
                className="size-[15px] shrink-0 text-[#00e599]"
                aria-hidden="true"
              />
              <span>{toastMessage}</span>
            </div>
            <button
              type="button"
              onClick={() => setToastMessage(null)}
              className="font-mono text-[11px] text-[#94a3b8] hover:text-[#f8fafc]"
            >
              Dismiss
            </button>
          </div>
        )}
      </div>

      <LinkRepositoriesModal
        open={linkModalOpen}
        onClose={onCloseLinkModal}
        project={project}
        githubIntegrations={activeGitHubIntegrations}
        selectedIntegrationId={selectedIntegrationId}
        onLinked={(linkedNames) =>
          setToastMessage(
            linkedNames.length === 1
              ? `Linked ${linkedNames[0]} to ${project.name}.`
              : `Linked ${linkedNames.length} repositories to ${project.name}.`,
          )
        }
      />

      <UnlinkRepositoryModal
        open={Boolean(unlinkTarget)}
        onClose={() => setUnlinkTarget(null)}
        projectId={project.id}
        projectName={project.name}
        repository={unlinkTarget}
        onUnlinked={(fullName) =>
          setToastMessage(`Unlinked ${fullName} from ${project.name}.`)
        }
      />
    </div>
  );
}
