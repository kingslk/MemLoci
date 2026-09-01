import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Archive } from "lucide-react";
import { getEvidence, getMemories } from "../api";
import { Button, Card, PageHeader } from "../components/ui";
import { jobActivity, showStatus } from "../lib/labels";
import { useWorkspace } from "../workspace";

export function OverviewPage() {
  const { project, projectId, repositories, jobs } = useWorkspace();
  const navigate = useNavigate();
  const memoriesQuery = useQuery({
    queryKey: ["memories", projectId, "overview"],
    queryFn: () => getMemories(projectId!, { page: 1, pageSize: 1 }),
    enabled: Boolean(projectId),
  });
  const reviewQuery = useQuery({
    queryKey: ["memories", projectId, "review-count"],
    queryFn: () => getMemories(projectId!, { status: "review", page: 1, pageSize: 1 }),
    enabled: Boolean(projectId),
  });
  const evidenceQuery = useQuery({
    queryKey: ["evidence", projectId, "overview"],
    queryFn: () => getEvidence(projectId!, { page: 1, pageSize: 1 }),
    enabled: Boolean(projectId),
  });
  const reviewCount = reviewQuery.data?.total ?? 0;
  return (
    <div className="grid gap-9">
      <PageHeader
        kicker="概览"
        title={project?.name ?? "选择项目"}
        description={project?.description || "从 GitLab 变更里留下可复用经验，并写清换项目时别照搬什么。"}
        actions={
          <>
            <Button onClick={() => navigate("/review")}>去待审{reviewCount ? ` ${reviewCount}` : ""}</Button>
            <Button variant="secondary" onClick={() => navigate("/memories")}>记忆库</Button>
            <Button variant="secondary" onClick={() => navigate("/evidence")}>证据</Button>
            <Button variant="secondary" onClick={() => navigate("/graph")}>图谱</Button>
          </>
        }
      />
      <section className="grid grid-cols-2 border-y border-white/12 md:grid-cols-4">
        <Metric label="仓库" value={repositories.length} />
        <Metric label="待审" value={reviewCount} />
        <Metric label="经验" value={memoriesQuery.data?.total ?? 0} />
        <Metric label="证据" value={evidenceQuery.data?.total ?? 0} />
      </section>
      <section className="grid gap-3">
        {repositories.map((item) => (
          <button key={item.id} className="card card-hover flex items-center gap-4 p-5 text-left" onClick={() => navigate("/repositories")}>
            <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-white/14 text-mute">
              <Archive size={18} strokeWidth={1.8} />
            </span>
            <div className="min-w-0 flex-1">
              <strong className="font-medium tracking-tight">{item.name}</strong>
              <p className="mt-1 text-sm text-mute">{item.release_branch} · {item.last_remote_sha?.slice(0, 8) || "未同步"}</p>
            </div>
            <span className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-mute">{repoJobLabel(item.id, item.sync_status, jobs)}</span>
          </button>
        ))}
        {!repositories.length && (
          <Card className="p-6">
            <p className="text-sm text-mute">还没有仓库。</p>
            <Button className="mt-4" variant="secondary" onClick={() => navigate("/repositories")}>去绑定</Button>
          </Card>
        )}
      </section>
      {jobs.some((job) => ["queued", "running"].includes(job.status)) && (
        <p className="text-sm text-mute">有任务在跑，细节在任务页。</p>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="border-white/12 px-5 py-7 [&:nth-child(even)]:border-l md:[&:not(:first-child)]:border-l">
      <p className="text-sm text-mute">{label}</p>
      <p className="mt-3 text-4xl font-semibold tracking-[-0.04em]">{value}</p>
    </div>
  );
}

function repoJobLabel(
  repositoryId: number,
  syncStatus: string,
  jobs: ReturnType<typeof useWorkspace>["jobs"],
) {
  const job = jobs.find(
    (entry) => entry.repository_id === repositoryId && ["queued", "running", "paused"].includes(entry.status),
  );
  if (!job) return showStatus(syncStatus);
  return jobActivity(job);
}
