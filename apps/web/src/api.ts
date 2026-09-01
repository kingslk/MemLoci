// 集中处理 HTTP、认证头和错误归一化；组件只关心业务数据和缓存失效。

export type Project = {
  id: number;
  name: string;
  description: string;
};

export type Repository = {
  id: number;
  project_id: number;
  name: string;
  gitlab_project_id: string;
  clone_url: string;
  release_branch: string;
  ignore: string[];
  active: boolean;
  sync_status: string;
  last_synced_sha: string | null;
  last_remote_sha: string | null;
  sync_error: string | null;
};

export type Memory = {
  id: number;
  project_id?: number;
  title: string;
  status: string;
  confidence: number;
  problem: string;
  pattern: string[];
  implementation?: Record<string, unknown>;
  do_not_copy: string[];
  apply_when: string[];
  do_not: string[];
  scope: Record<string, unknown>;
  repository_id: number | null;
  topic_id?: number | null;
  version: number;
  evidence_count?: number;
  repository_name?: string | null;
  topic_name?: string | null;
  updated_at?: string | null;
};

export type MemoryDetail = {
  memory: Memory;
  evidence: Evidence[];
  audits: Array<{
    id: number;
    action: string;
    actor: string;
    reason: string;
    created_at: string;
    before: Record<string, unknown>;
    after: Record<string, unknown>;
  }>;
};

export type Evidence = {
  id: number;
  repository_id: number;
  release_change_id: number | null;
  source_type: string;
  source_id: string;
  title: string;
  summary: string;
  importance_score: number;
  payload: Record<string, unknown>;
  files?: string[];
  diff?: string;
};

export type EvidenceDetail = Evidence & {
  diff: string;
  files: Array<string | { path: string; change_type?: string; additions?: number; deletions?: number }>;
  memories: Array<{ id: number; title: string; status: string }>;
};

export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};

export type Dream = {
  id: number;
  project_id: number;
  dream_type: string;
  status: string;
  output_summary: Record<string, unknown>;
  error: string | null;
};

export type Job = {
  id: number;
  project_id: number | null;
  repository_id: number | null;
  kind: string;
  status: string;
  current_stage: string;
  progress: number;
  retry_count: number;
  checkpoint: {
    completed_stages?: string[];
    stage_results?: Record<string, unknown>;
    completed?: number;
    total?: number;
    percent?: number;
    stage?: string;
    detail?: string;
  };
  error: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type Topic = {
  id: number;
  name: string;
  key: string;
};

export type DreamChange = {
  id: number;
  memory_id: number | null;
  action: string;
  reason: string;
  status: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  evidence_ids: number[];
};

export type DreamDetail = Dream & {
  provider?: string;
  model?: string;
  prompt_version?: string;
  duration_ms?: number;
  changes: DreamChange[];
};

export type GraphNode = {
  id: string;
  label: string;
  kind: string;
  subtype?: string;
  path?: string;
  summary?: string;
  topic?: string;
  confidence?: number;
  status?: string;
  repository_id?: number | null;
};

export type GraphData = {
  nodes: GraphNode[];
  edges: Array<{
    id: string;
    source: string;
    target: string;
    type: string;
    confidence?: number;
    inferred?: boolean;
  }>;
  meta?: {
    prefix?: string;
    parent?: string;
    file_total?: number;
    dir_count?: number;
    file_count?: number;
    truncated?: boolean;
    kind?: string;
    focus?: string;
    path?: string;
    symbol_count?: number;
  };
};

export type QueryLog = {
  id: number;
  session_id: string;
  project_id: number;
  repository_id: number | null;
  repository_name: string | null;
  tool_name: string;
  task: string;
  recall_mode: string;
  primary_switched: boolean;
  returned_count: number;
  searched_repository_count?: number | null;
  result_repository_count?: number | null;
  results_by_repository?: Array<{ name: string; count: number }>;
  latency_ms: number;
  token_budget: number;
  input_summary: Record<string, unknown>;
  output_summary: {
    memory_ids?: number[];
    recall_mode?: string;
    scope?: Record<string, unknown>;
    cutoff?: Record<string, unknown>;
    searched_repository_count?: number;
    result_repository_count?: number;
    results_by_repository?: Array<{ name: string; count: number }>;
    results?: Array<Record<string, unknown>>;
    hint?: string | null;
  };
  created_at: string | null;
};

const apiFetch = async <T>(path: string, options?: RequestInit): Promise<T> => {
  // Token 只进 sessionStorage，不写 localStorage，也不用 VITE_* 打进前端包。
  const adminToken =
    typeof window === "undefined"
      ? ""
      : window.sessionStorage.getItem("memloci_admin_token") ?? "";
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(adminToken ? { "X-Admin-Token": adminToken } : {}),
      ...(options?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败：${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
};

export const getProjects = () => apiFetch<Project[]>("/api/v1/projects");

export const getRepositories = (projectId: number) =>
  apiFetch<Repository[]>(`/api/v1/projects/${projectId}/repositories`);

export const getMemories = (
  projectId: number,
  params?: {
    repositoryId?: number;
    status?: string;
    topicId?: number;
    q?: string;
    page?: number;
    pageSize?: number;
  },
) => {
  const search = new URLSearchParams();
  if (params?.repositoryId) search.set("repository_id", String(params.repositoryId));
  if (params?.status) search.set("status", params.status);
  if (params?.topicId) search.set("topic_id", String(params.topicId));
  if (params?.q) search.set("q", params.q);
  search.set("page", String(params?.page ?? 1));
  search.set("page_size", String(params?.pageSize ?? 20));
  return apiFetch<Page<Memory>>(`/api/v1/projects/${projectId}/memories?${search.toString()}`);
};

export const getTopics = (projectId: number) =>
  apiFetch<Topic[]>(`/api/v1/projects/${projectId}/topics`);

export const getMemoryDetail = (memoryId: number) =>
  apiFetch<MemoryDetail>(`/api/v1/memories/${memoryId}`);

export const getEvidence = (
  projectId: number,
  params?: { repositoryId?: number; page?: number; pageSize?: number },
) => {
  const search = new URLSearchParams();
  if (params?.repositoryId) search.set("repository_id", String(params.repositoryId));
  search.set("page", String(params?.page ?? 1));
  search.set("page_size", String(params?.pageSize ?? 20));
  return apiFetch<Page<Evidence>>(`/api/v1/projects/${projectId}/evidence?${search.toString()}`);
};

export const getEvidenceDetail = (evidenceId: number) =>
  apiFetch<EvidenceDetail>(`/api/v1/evidence/${evidenceId}`);

export const getDreams = (projectId: number, params?: { page?: number; pageSize?: number }) => {
  const search = new URLSearchParams();
  search.set("page", String(params?.page ?? 1));
  search.set("page_size", String(params?.pageSize ?? 20));
  return apiFetch<Page<Dream>>(`/api/v1/projects/${projectId}/dreams?${search.toString()}`);
};

export const getJobs = () => apiFetch<Job[]>("/api/v1/jobs");

export const subscribeJobs = async (
  onJobs: (jobs: Job[]) => void,
  signal?: AbortSignal,
): Promise<void> => {
  const adminToken =
    typeof window === "undefined"
      ? ""
      : window.sessionStorage.getItem("memloci_admin_token") ?? "";
  const response = await fetch("/api/v1/jobs/stream", {
    headers: adminToken ? { "X-Admin-Token": adminToken } : {},
    signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败：${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const dataLine = chunk.split("\n").find((line) => line.startsWith("data:"));
      if (!dataLine) continue;
      onJobs(JSON.parse(dataLine.slice(5).trim()) as Job[]);
    }
  }
};

export const getGraph = (
  projectId: number,
  kind: "code" | "memory" | "combined" | "neighborhood",
  options?: { repositoryId?: number; focus?: string; prefix?: string; fileId?: number; limit?: number },
) => {
  const params = new URLSearchParams();
  if (options?.repositoryId) params.set("repository_id", String(options.repositoryId));
  if (options?.focus) params.set("focus", options.focus);
  if (options?.prefix) params.set("prefix", options.prefix);
  if (options?.fileId) params.set("file_id", String(options.fileId));
  params.set("limit", String(options?.limit ?? (kind === "code" ? 400 : 80)));
  return apiFetch<GraphData>(
    `/api/v1/projects/${projectId}/graphs/${kind}?${params.toString()}`,
  );
};

export const getQueryLogs = (
  projectId: number,
  params?: {
    recallMode?: string;
    primarySwitched?: boolean;
    sessionId?: string;
    page?: number;
    pageSize?: number;
  },
) => {
  const search = new URLSearchParams();
  if (params?.recallMode) search.set("recall_mode", params.recallMode);
  if (params?.primarySwitched != null) search.set("primary_switched", String(params.primarySwitched));
  if (params?.sessionId) search.set("session_id", params.sessionId);
  search.set("page", String(params?.page ?? 1));
  search.set("page_size", String(params?.pageSize ?? 20));
  return apiFetch<Page<QueryLog>>(`/api/v1/projects/${projectId}/query-logs?${search.toString()}`);
};

export const clearQueryLogs = (projectId: number) =>
  apiFetch<{ deleted: number }>(`/api/v1/projects/${projectId}/query-logs`, { method: "DELETE" });

export const batchCorrectMemories = (
  payload: { memory_ids: number[]; status: string; reason: string },
) =>
  apiFetch<{ results: Array<{ id: number; ok: boolean; status?: string; error?: string }> }>(
    "/api/v1/memories/batch-correct",
    { method: "POST", body: JSON.stringify(payload) },
  );

export const createProject = (payload: { name: string; description: string }) =>
  apiFetch<Project>("/api/v1/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateProject = (
  projectId: number,
  payload: { name: string; description: string },
) =>
  apiFetch<Project>(`/api/v1/projects/${projectId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

export type RepositoryPayload = {
  name: string;
  gitlab_project_id: string;
  clone_url: string;
  release_branch: string;
  ignore: string[];
};

export const createRepository = (projectId: number, payload: RepositoryPayload) =>
  apiFetch<Repository>(`/api/v1/projects/${projectId}/repositories`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updateRepository = (repositoryId: number, payload: RepositoryPayload) =>
  apiFetch<Repository>(`/api/v1/repositories/${repositoryId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });

export const connectionTest = (repositoryId: number) =>
  apiFetch<{ ok?: boolean; status?: string }>(
    `/api/v1/repositories/${repositoryId}/connection-test`,
    { method: "POST" },
  );

export const syncRepository = (repositoryId: number) =>
  apiFetch<Job>(`/api/v1/repositories/${repositoryId}/sync`, {
    method: "POST",
  });

export const reconcileRepository = (repositoryId: number) =>
  apiFetch<Record<string, unknown>>(
    `/api/v1/repositories/${repositoryId}/reconcile`,
    { method: "POST" },
  );

export const historySync = (repositoryId: number) =>
  apiFetch<Job>(
    `/api/v1/repositories/${repositoryId}/history-sync`,
    { method: "POST" },
  );

export const correctMemory = (
  memoryId: number,
  payload: {
    status?: string;
    confidence?: number;
    title?: string;
    problem?: string;
    implementation?: Record<string, unknown>;
    pattern?: string[];
    do_not_copy?: string[];
    apply_when?: string[];
    do_not?: string[];
    scope?: Record<string, unknown>;
    reason: string;
  },
) =>
  apiFetch<Memory>(`/api/v1/memories/${memoryId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const runDream = (projectId: number, dreamType = "manual") =>
  apiFetch<Job>("/api/v1/dreams", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, dream_type: dreamType }),
  });

export const getDreamDetail = (dreamId: number) =>
  apiFetch<DreamDetail>(`/api/v1/dreams/${dreamId}`);

export const revertDreamChange = (changeId: number, reason: string) =>
  apiFetch<{ id: number; status: string }>(
    `/api/v1/dream-changes/${changeId}/revert?reason=${encodeURIComponent(reason)}`,
    { method: "POST" },
  );

export const createInitialization = (projectId: number, repositoryId?: number) =>
  apiFetch<Job>(`/api/v1/initializations?project_id=${projectId}`, {
    method: "POST",
    body: JSON.stringify(repositoryId ? { repository_id: repositoryId } : {}),
  });

export const runJob = (jobId: number) =>
  apiFetch<Job>(`/api/v1/jobs/${jobId}/run`, { method: "POST" });

export const pauseJob = (jobId: number) =>
  apiFetch<Job>(`/api/v1/jobs/${jobId}/pause`, { method: "POST" });

export const cancelJob = (jobId: number) =>
  apiFetch<Job>(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" });

export const retryJob = (jobId: number) =>
  apiFetch<Job>(`/api/v1/jobs/${jobId}/retry`, { method: "POST" });

export const deleteJob = (jobId: number) =>
  apiFetch<void>(`/api/v1/jobs/${jobId}`, { method: "DELETE" });
