import { FormEvent, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  connectionTest,
  createInitialization,
  createRepository,
  historySync,
  reconcileRepository,
  RepositoryPayload,
  syncRepository,
  updateRepository,
} from "../api";
import { Button, Card, Dialog, Field, Input, PageHeader, Textarea } from "../components/ui";
import { Job } from "../api";
import { jobActivity, showStatus } from "../lib/labels";
import { useWorkspace } from "../workspace";

const emptyForm = {
  name: "",
  gitlab_project_id: "",
  clone_url: "",
  release_branch: "main",
  ignoreText: "dist/**\n**/*.min.js",
};

export function RepositoriesPage() {
  const { projectId, repositories, jobs, refresh } = useWorkspace();
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState<number>();
  const [creating, setCreating] = useState(false);
  const [confirmInit, setConfirmInit] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const selected = repositories.find((item) => item.id === (selectedId ?? repositories[0]?.id));

  const createMutation = useMutation({
    mutationFn: (payload: RepositoryPayload) => createRepository(projectId!, payload),
    onSuccess: (created) => {
      refresh();
      setSelectedId(created.id);
      setCreating(false);
      setForm(emptyForm);
      toast.success("仓库已绑定");
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const updateMutation = useMutation({
    mutationFn: (payload: RepositoryPayload) => updateRepository(selected!.id, payload),
    onSuccess: () => {
      refresh();
      toast.success("设置已保存");
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const actionMutation = useMutation({
    mutationFn: async (action: "connection" | "sync" | "reconcile" | "history") => {
      if (!selected) throw new Error("请先选择仓库");
      if (action === "connection") return connectionTest(selected.id);
      if (action === "sync") return syncRepository(selected.id);
      if (action === "reconcile") return reconcileRepository(selected.id);
      return historySync(selected.id);
    },
    onSuccess: () => {
      refresh();
      toast.success("操作已提交");
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const initMutation = useMutation({
    mutationFn: () => createInitialization(projectId!, selected!.id),
    onSuccess: () => {
      refresh();
      toast.success("初始化任务已创建", {
        description: `只处理 ${selected?.name ?? "当前仓库"}`,
        action: { label: "查看任务", onClick: () => navigate("/jobs") },
      });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const submitCreate = (event: FormEvent) => {
    event.preventDefault();
    createMutation.mutate({
      ...form,
      ignore: form.ignoreText.split("\n").map((item) => item.trim()).filter(Boolean),
    });
  };

  return (
    <div className="grid gap-6">
      <PageHeader
        kicker="仓库"
        title="绑定 GitLab"
        description="实例级 Token 在 .env，这里只填这个仓库自己的 ID、地址和分支。"
        actions={<Button onClick={() => setCreating((value) => !value)}>{creating ? "收起" : "新增仓库"}</Button>}
      />
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="grid content-start gap-2">
          {repositories.map((item) => (
            <button
              key={item.id}
              className={`card p-4 text-left ${item.id === selected?.id ? "bg-white/8" : "card-hover"}`}
              onClick={() => { setSelectedId(item.id); setCreating(false); }}
            >
              <strong className="text-sm font-medium">{item.name}</strong>
              <p className="mt-1 text-xs text-faint">{item.release_branch} · {repoSyncLabel(item.id, item.sync_status, jobs)}</p>
            </button>
          ))}
          {!repositories.length && <p className="text-sm text-faint">还没有仓库。</p>}
        </div>
        <Card className="p-6">
          {creating || !selected ? (
            <form className="grid gap-3" onSubmit={submitCreate}>
              <h3 className="text-lg font-semibold">新增仓库</h3>
              <Field label="名称"><Input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field>
              <Field label="GitLab 项目 ID"><Input required value={form.gitlab_project_id} onChange={(event) => setForm({ ...form, gitlab_project_id: event.target.value })} /></Field>
              <Field label="克隆地址"><Input required type="url" value={form.clone_url} onChange={(event) => setForm({ ...form, clone_url: event.target.value })} /></Field>
              <Field label="正式分支"><Input required value={form.release_branch} onChange={(event) => setForm({ ...form, release_branch: event.target.value })} /></Field>
              <Field label="忽略规则" hint="每行一条；默认已过滤图片、release、.next、dist、node_modules 和 *.min.js。"><Textarea rows={4} value={form.ignoreText} onChange={(event) => setForm({ ...form, ignoreText: event.target.value })} /></Field>
              <IgnoreHelp />
              <Button disabled={!projectId || createMutation.isPending}>{createMutation.isPending ? "保存中…" : "绑定仓库"}</Button>
            </form>
          ) : (
            <RepositoryEditor
              key={selected.id}
              repository={selected}
              saving={updateMutation.isPending}
              actionPending={actionMutation.isPending || initMutation.isPending}
              syncJob={activeRepoJob(selected.id, jobs)}
              onSave={(payload) => updateMutation.mutate(payload)}
              onAction={(action) => actionMutation.mutate(action)}
              onInitialize={() => setConfirmInit(true)}
            />
          )}
        </Card>
      </div>
      <Dialog
        open={confirmInit}
        title="全量初始化此仓库？"
        confirmLabel="创建"
        onClose={() => setConfirmInit(false)}
        onConfirm={() => {
          initMutation.mutate();
          setConfirmInit(false);
        }}
      >
        只扫描、导入并初始化「{selected?.name}」，不会重跑项目组里其他仓库。
      </Dialog>
    </div>
  );
}

function RepositoryEditor({
  repository,
  saving,
  actionPending,
  syncJob,
  onSave,
  onAction,
  onInitialize,
}: {
  repository: ReturnType<typeof useWorkspace>["repositories"][number];
  saving: boolean;
  actionPending: boolean;
  syncJob?: Job;
  onSave: (payload: RepositoryPayload) => void;
  onAction: (action: "connection" | "sync" | "reconcile" | "history") => void;
  onInitialize: () => void;
}) {
  const [form, setForm] = useState({
    name: repository.name,
    gitlab_project_id: repository.gitlab_project_id,
    clone_url: repository.clone_url,
    release_branch: repository.release_branch,
    ignoreText: repository.ignore.join("\n"),
  });
  return (
    <div className="grid gap-5 md:grid-cols-2">
      <form
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          onSave({
            ...form,
            ignore: form.ignoreText.split("\n").map((item) => item.trim()).filter(Boolean),
          });
        }}
      >
        <h3 className="text-lg font-semibold">{repository.name}</h3>
        <Field label="名称"><Input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field>
        <Field label="GitLab 项目 ID"><Input required value={form.gitlab_project_id} onChange={(event) => setForm({ ...form, gitlab_project_id: event.target.value })} /></Field>
        <Field label="克隆地址"><Input required type="url" value={form.clone_url} onChange={(event) => setForm({ ...form, clone_url: event.target.value })} /></Field>
        <Field label="正式分支"><Input required value={form.release_branch} onChange={(event) => setForm({ ...form, release_branch: event.target.value })} /></Field>
        <Field label="忽略规则" hint="每行一条；默认已过滤图片、release、.next、dist、node_modules 和 *.min.js。"><Textarea rows={4} value={form.ignoreText} onChange={(event) => setForm({ ...form, ignoreText: event.target.value })} /></Field>
        <IgnoreHelp />
        <Button disabled={saving}>{saving ? "保存中…" : "保存设置"}</Button>
      </form>
      <div className="grid content-start gap-3">
        <div className="rounded-2xl bg-white/5 px-3 py-2 text-sm">
          <span className="text-faint">远端</span>
          <code className="ml-2">{repository.last_remote_sha?.slice(0, 12) || "—"}</code>
        </div>
        <div className="rounded-2xl bg-white/5 px-3 py-2 text-sm">
          <span className="text-faint">镜像</span>
          <code className="ml-2">{repository.last_synced_sha?.slice(0, 12) || "—"}</code>
        </div>
        {repository.sync_error && <p className="text-sm text-red-400">{repository.sync_error}</p>}
        {syncJob && (
          <div className="rounded-2xl bg-white/5 px-3 py-3">
            <div className="flex items-center justify-between text-xs text-mute">
              <span className="truncate">{jobActivity(syncJob)}</span>
            </div>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/10">
              <span className="block h-full bg-white" style={{ width: `${Math.round(syncJob.progress * 100)}%` }} />
            </div>
          </div>
        )}
        <Button variant="secondary" disabled={actionPending} onClick={() => onAction("connection")}>连接测试</Button>
        <Button variant="secondary" disabled={actionPending || Boolean(syncJob)} onClick={() => onAction("sync")}>{syncJob ? "同步中…" : "同步代码镜像"}</Button>
        <Button variant="secondary" disabled={actionPending} onClick={() => onAction("history")}>导入历史证据</Button>
        <Button variant="secondary" disabled={actionPending} onClick={() => onAction("reconcile")}>正式分支对账</Button>
        <Button disabled={actionPending || Boolean(syncJob)} onClick={onInitialize}>{syncJob ? "任务进行中…" : "全量初始化此仓库"}</Button>
      </div>
    </div>
  );
}

function IgnoreHelp() {
  return (
    <details className="rounded-xl border border-white/10 px-3 py-2 text-xs text-mute">
      <summary className="cursor-pointer text-ink">规则怎么写</summary>
      <div className="mt-2 grid gap-1 leading-5">
        <p><code>release/**</code>：过滤任意层级的 release 目录；等同 <code>**/release/**</code>。</p>
        <p><code>/release/**</code>：只过滤仓库根目录下的 release。</p>
        <p><code>**/*.min.js</code>：过滤任意层级的压缩 JS。</p>
        <p><code>!release/keep.js</code>：把匹配文件重新纳入；后写规则覆盖前面的规则。</p>
        <p><code># 说明</code>：注释，不参与匹配。</p>
      </div>
    </details>
  );
}

const liveJobStatus = new Set(["queued", "running", "paused", "cancel_requested"]);

function activeRepoJob(repositoryId: number, jobs: Job[]) {
  return jobs.find((job) => job.repository_id === repositoryId && liveJobStatus.has(job.status));
}

function repoSyncLabel(repositoryId: number, syncStatus: string, jobs: Job[]) {
  const job = activeRepoJob(repositoryId, jobs);
  if (!job) return showStatus(syncStatus);
  return jobActivity(job);
}
