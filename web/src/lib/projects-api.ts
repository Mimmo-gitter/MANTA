import { apiRequest } from "@/lib/api-client";
import type {
  DeploymentSignalConfig,
  DeploymentSignalUpdatePayload,
  ProjectCreatePayload,
  ProjectIncidentItem,
  ProjectItem,
  ProjectUpdatePayload,
  RepositoryItem,
  RepositoryLinkPayload,
} from "@/types/project";

const PROJECT_SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const OWNER_PATTERN = /^[a-zA-Z0-9_](?:[a-zA-Z0-9_-]*[a-zA-Z0-9_])?$/;
const REPO_PATTERN = /^[a-zA-Z0-9_.-]+$/;
const SSH_REPO_REGEX =
  /^git@github\.com:([a-zA-Z0-9_](?:[a-zA-Z0-9_-]*[a-zA-Z0-9_])?)\/([a-zA-Z0-9_.-]+?)(?:\.git)?$/;

export async function listProjects(): Promise<ProjectItem[]> {
  return apiRequest<ProjectItem[]>("/api/v1/projects");
}

export async function getProject(projectId: string): Promise<ProjectItem> {
  return apiRequest<ProjectItem>(`/api/v1/projects/${projectId}`);
}

export async function createProject(
  payload: ProjectCreatePayload,
): Promise<ProjectItem> {
  return apiRequest<ProjectItem>("/api/v1/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateProject(
  projectId: string,
  payload: ProjectUpdatePayload,
): Promise<ProjectItem> {
  return apiRequest<ProjectItem>(`/api/v1/projects/${projectId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteProject(projectId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/projects/${projectId}`, {
    method: "DELETE",
  });
}

export async function listProjectIncidents(
  projectId: string,
): Promise<ProjectIncidentItem[]> {
  return apiRequest<ProjectIncidentItem[]>(
    `/api/v1/projects/${projectId}/incidents`,
  );
}

export async function linkRepository(
  projectId: string,
  payload: RepositoryLinkPayload,
): Promise<RepositoryItem> {
  return apiRequest<RepositoryItem>(`/api/v1/projects/${projectId}/repositories`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function unlinkRepository(
  projectId: string,
  repoId: string,
): Promise<void> {
  return apiRequest<void>(`/api/v1/projects/${projectId}/repositories/${repoId}`, {
    method: "DELETE",
  });
}

export async function getDeploymentSignal(
  projectId: string,
): Promise<DeploymentSignalConfig> {
  return apiRequest<DeploymentSignalConfig>(
    `/api/v1/projects/${projectId}/deployment-signal`,
  );
}

export async function updateDeploymentSignal(
  projectId: string,
  payload: DeploymentSignalUpdatePayload,
): Promise<DeploymentSignalConfig> {
  return apiRequest<DeploymentSignalConfig>(
    `/api/v1/projects/${projectId}/deployment-signal`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
  );
}

export function slugifyProjectName(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/-+$/g, "");
}

export function isValidProjectSlug(slug: string): boolean {
  return slug.length > 0 && slug.length <= 64 && PROJECT_SLUG_PATTERN.test(slug);
}

export function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    value,
  );
}

export type NormalizedGitHubRepoResult =
  | { ok: true; fullName: string; repositoryUrl: string }
  | { ok: false; error: string };

/**
 * Mirrors backend/app/projects/validators.py so UI feedback matches API validation.
 */
export function normalizeGitHubRepoInput(
  rawInput: string,
): NormalizedGitHubRepoResult {
  const clean = rawInput.trim();
  if (!clean) {
    return { ok: false, error: "Repository identifier cannot be empty." };
  }

  const sshMatch = SSH_REPO_REGEX.exec(clean);
  if (sshMatch) {
    const [, owner, rawRepo] = sshMatch;
    const repo = rawRepo.replace(/\.git$/i, "");
    if (!repo || repo === "." || repo === "..") {
      return { ok: false, error: `Invalid GitHub repository name in '${clean}'.` };
    }
    const fullName = `${owner}/${repo}`;
    return {
      ok: true,
      fullName,
      repositoryUrl: `https://github.com/${fullName}`,
    };
  }

  if (
    clean.startsWith("http://") ||
    clean.startsWith("https://") ||
    clean.includes("://") ||
    clean.includes("github.com/")
  ) {
    const urlToParse = clean.includes("://") ? clean : `https://${clean}`;
    let parsed: URL;
    try {
      parsed = new URL(urlToParse);
    } catch {
      return { ok: false, error: `Malformed repository URL: '${clean}'.` };
    }

    const host = parsed.hostname.toLowerCase();
    if (host !== "github.com" && host !== "www.github.com") {
      return {
        ok: false,
        error: `Only GitHub repositories are supported. Received host '${host}'.`,
      };
    }

    const parts = parsed.pathname.split("/").filter(Boolean);
    if (parts.length !== 2) {
      return {
        ok: false,
        error: `Invalid GitHub repository path: '${parsed.pathname}'. Expected '/owner/repo'.`,
      };
    }

    const owner = parts[0];
    const repo = parts[1].replace(/\.git$/i, "");
    if (
      !OWNER_PATTERN.test(owner) ||
      !REPO_PATTERN.test(repo) ||
      repo === "." ||
      repo === ".."
    ) {
      return {
        ok: false,
        error: `Invalid GitHub repository name or owner in '${clean}'.`,
      };
    }

    const fullName = `${owner}/${repo}`;
    return {
      ok: true,
      fullName,
      repositoryUrl: `https://github.com/${fullName}`,
    };
  }

  const normalizedSlug = clean.replace(/\.git$/i, "");
  const segments = normalizedSlug.split("/");
  if (segments.length !== 2) {
    return {
      ok: false,
      error: "Use 'owner/repo', 'https://github.com/owner/repo', or SSH URL.",
    };
  }

  const [owner, repo] = segments;
  if (
    !OWNER_PATTERN.test(owner) ||
    !REPO_PATTERN.test(repo) ||
    repo === "." ||
    repo === ".."
  ) {
    return {
      ok: false,
      error: `Invalid GitHub repository identifier '${clean}'.`,
    };
  }

  const fullName = `${owner}/${repo}`;
  return {
    ok: true,
    fullName,
    repositoryUrl: `https://github.com/${fullName}`,
  };
}
