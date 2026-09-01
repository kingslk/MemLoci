import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { cancelJob, createInitialization, deleteJob, pauseJob, retryJob, runJob } from "../api";
import { Badge, Button, Card, Dialog, Empty, PageHeader, StatusTone } from "../components/ui";
import { jobActivity, jobEta, showJobKind, showStatus } from "../lib/labels";
import { useWorkspace } from "../workspace";

export function JobsPage() {
  const { projectId, projects, jobs, refresh } = useWorkspace();
  const [confirmInit, setConfirmInit] = useState(false);
  const createMutation = useMutation({
    mutationFn: () => createInitialization(projectId!),
    onSuccess: () => {
      refresh();
      toast.success("初始化任务已创建");
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const actionMutation = useMutation({
    mutationFn: async ({
      action,
      id,
    }: {
      action: "run" | "pause" | "cancel" | "retry" | "delete";
      id: number;
    }) => {
      if (action === "run") return runJob(id);
      if (action === "pause") return pauseJob(id);
      if (action === "cancel") return cancelJob(id);
      if (action === "delete") return deleteJob(id);
      return retryJob(id);
    },
    onSuccess: (_, variables) => {
      refresh();
      toast.success(variables.action === "delete" ? "任务已删除" : "任务已更新");
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const projectGroups = useMemo(() => {
    const names = new Map(projects.map((project) => [project.id, project.name]));
    const grouped = new Map<number | null, typeof jobs>();
    for (const job of jobs) {
      const key = job.project_id;
      grouped.set(key, [...(grouped.get(key) ?? []), job]);
    }
    return [...grouped].map(([id, items]) => ({
      id,
      name: id == null ? "未归属项目" : names.get(id) ?? `项目 #${id}`,
      jobs: items,
    }));
  }, [jobs, projects]);

  return (
    <div className="grid gap-6">
      <PageHeader
        kicker="任务"
        title="同步与整理任务"
        description="仓库同步、初始化和整理的进度。这里列出全部项目的任务；新建初始化仍作用在当前项目。"
        actions={<Button disabled={!projectId || createMutation.isPending} onClick={() => setConfirmInit(true)}>创建初始化任务</Button>}
      />
      <div className="grid grid-cols-2 gap-2 text-xs text-faint md:grid-cols-4">
        {["扫描当前代码", "导入完整历史", "分析架构阶段", "重建主题"].map((item, index) => (
          <div key={item} className="rounded-2xl bg-white/5 px-3 py-2">{index + 1}. {item}</div>
        ))}
      </div>
      <p className="text-xs text-faint">初始化成功后会自动排队「打磨近邻记忆」，只合并文件和用词都撞车的条目。</p>
      <div className="grid gap-6">
        {projectGroups.map((group) => (
          <section key={group.id ?? "none"} className="grid gap-3">
            <div className="flex items-end justify-between border-b border-white/10 pb-2">
              <div>
                <p className="text-xs text-faint">PROJECT</p>
                <h2 className="text-lg font-medium">{group.name}</h2>
              </div>
              <span className="text-xs text-faint">{group.jobs.length} 个任务</span>
            </div>
            {group.jobs.map((job) => (
              <Card key={job.id} className="p-5">
                <div className="flex items-center justify-between">
                  <strong className="font-medium">任务 #{job.id} · {showJobKind(job.kind)}</strong>
                  <Badge tone={StatusTone(job.status)}>{showStatus(job.status)}</Badge>
                </div>
                <p className="mt-1 text-xs text-faint">{job.repository_id ? `仓库 #${job.repository_id}` : "项目级任务"}</p>
                <div className="mt-3 h-1 overflow-hidden rounded-full bg-white/10">
                  <span className="block h-full bg-white" style={{ width: `${Math.min(100, Math.round(job.progress * 100))}%` }} />
                </div>
                <div className="mt-2 flex flex-wrap justify-between gap-2 text-xs text-faint">
                  <span>{jobActivity(job)}</span>
                  <span>{jobEta(job)}</span>
                </div>
                {job.error && <p className="mt-2 text-sm text-red-300">{job.error}</p>}
                <div className="mt-3 flex gap-3">
                  {job.status === "paused" && <Button disabled={actionMutation.isPending} variant="ghost" className="px-0" onClick={() => actionMutation.mutate({ action: "run", id: job.id })}>继续</Button>}
                  {job.status === "running" && <Button variant="ghost" className="px-0" onClick={() => actionMutation.mutate({ action: "pause", id: job.id })}>暂停</Button>}
                  {["queued", "running", "paused", "retryable"].includes(job.status) && <Button variant="danger" className="px-0" onClick={() => actionMutation.mutate({ action: "cancel", id: job.id })}>取消</Button>}
                  {job.status === "cancel_requested" && <Button disabled variant="ghost" className="px-0">等待当前调用结束</Button>}
                  {["failed", "cancelled", "retryable"].includes(job.status) && <Button variant="ghost" className="px-0" onClick={() => actionMutation.mutate({ action: "retry", id: job.id })}>重试</Button>}
                  {["failed", "cancelled"].includes(job.status) && <Button variant="danger" className="px-0" onClick={() => actionMutation.mutate({ action: "delete", id: job.id })}>删除</Button>}
                </div>
              </Card>
            ))}
          </section>
        ))}
        {!jobs.length && <Empty text="当前没有后台任务。" />}
      </div>
      <Dialog
        open={confirmInit}
        title="创建初始化任务？"
        confirmLabel="创建"
        onClose={() => setConfirmInit(false)}
        onConfirm={() => {
          createMutation.mutate();
          setConfirmInit(false);
        }}
      >
        将扫描当前项目的全部仓库，已完成的阶段不会重跑。
      </Dialog>
    </div>
  );
}
