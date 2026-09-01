import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  createProject,
  getJobs,
  getProjects,
  getRepositories,
  Job,
  Project,
  Repository,
  subscribeJobs,
  updateProject,
} from "./api";

type WorkspaceValue = {
  projects: Project[];
  project?: Project;
  projectId?: number;
  setProjectId: (id: number) => void;
  repositories: Repository[];
  jobs: Job[];
  runningJob?: Job;
  hasToken: boolean;
  setToken: (token: string) => void;
  createProject: (payload: { name: string; description: string }) => void;
  saveProject: (payload: { name: string; description: string }) => void;
  refresh: () => void;
};

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [hasToken, setHasToken] = useState(
    () => Boolean(typeof window !== "undefined" && window.sessionStorage.getItem("memloci_admin_token")),
  );
  const [selectedProjectId, setSelectedProjectId] = useState<number | undefined>(() => {
    if (typeof window === "undefined") return undefined;
    const raw = window.sessionStorage.getItem("memloci_project_id");
    return raw ? Number(raw) : undefined;
  });
  const selectProject = (id: number) => {
    window.sessionStorage.setItem("memloci_project_id", String(id));
    setSelectedProjectId(id);
  };
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const projectId = selectedProjectId ?? projectsQuery.data?.[0]?.id;
  const project = projectsQuery.data?.find((item) => item.id === projectId);
  const repositoriesQuery = useQuery({
    queryKey: ["repositories", projectId],
    queryFn: () => getRepositories(projectId!),
    enabled: Boolean(projectId),
  });
  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: getJobs,
    enabled: hasToken,
  });

  useEffect(() => {
    if (!hasToken) return;
    const controller = new AbortController();
    let stopped = false;
    const applyJobs = (jobs: Job[]) => {
      const previous = client.getQueryData<Job[]>(["jobs"]) ?? [];
      client.setQueryData(["jobs"], jobs);
      const before = previous.map((job) => `${job.id}:${job.status}`).join("|");
      const after = jobs.map((job) => `${job.id}:${job.status}`).join("|");
      if (before !== after) {
        client.invalidateQueries({ queryKey: ["repositories"] });
        client.invalidateQueries({ queryKey: ["dreams"] });
        client.invalidateQueries({ queryKey: ["memories"] });
      }
    };
    const connect = async () => {
      while (!stopped) {
        try {
          await subscribeJobs(applyJobs, controller.signal);
        } catch {
          if (stopped || controller.signal.aborted) return;
        }
        if (stopped || controller.signal.aborted) return;
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
      }
    };
    void connect();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [client, hasToken]);

  const fail = (error: Error) => toast.error(error.message);
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["projects"] });
    client.invalidateQueries({ queryKey: ["jobs"] });
    if (!projectId) return;
    client.invalidateQueries({ queryKey: ["repositories", projectId] });
    client.invalidateQueries({ queryKey: ["memories", projectId] });
    client.invalidateQueries({ queryKey: ["evidence", projectId] });
    client.invalidateQueries({ queryKey: ["dreams", projectId] });
    client.invalidateQueries({ queryKey: ["topics", projectId] });
  };

  const createMutation = useMutation({
    mutationFn: createProject,
    onSuccess: (created) => {
      client.invalidateQueries({ queryKey: ["projects"] });
      selectProject(created.id);
      toast.success("项目已创建");
    },
    onError: fail,
  });
  const updateMutation = useMutation({
    mutationFn: (payload: { name: string; description: string }) => updateProject(projectId!, payload),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["projects"] });
      toast.success("项目已保存");
    },
    onError: fail,
  });

  const jobs = jobsQuery.data ?? [];
  const value = useMemo<WorkspaceValue>(
    () => ({
      projects: projectsQuery.data ?? [],
      project,
      projectId,
      setProjectId: selectProject,
      repositories: repositoriesQuery.data ?? [],
      jobs,
      runningJob: jobs.find((job) => ["queued", "running", "paused", "cancel_requested"].includes(job.status)),
      hasToken,
      setToken: (token: string) => {
        window.sessionStorage.setItem("memloci_admin_token", token);
        setHasToken(Boolean(token));
        client.invalidateQueries();
      },
      createProject: (payload) => createMutation.mutate(payload),
      saveProject: (payload) => updateMutation.mutate(payload),
      refresh,
    }),
    [projectsQuery.data, project, projectId, repositoriesQuery.data, jobs, hasToken],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("Workspace 未初始化");
  return value;
}
