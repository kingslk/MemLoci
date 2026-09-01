import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { getDreamDetail, getDreams, revertDreamChange, runDream } from "../api";
import { Badge, Button, Card, Dialog, Empty, PageHeader, Pager, StatusTone } from "../components/ui";
import { showDreamType, showStatus } from "../lib/labels";
import { useWorkspace } from "../workspace";

function asText(value: unknown) {
  return typeof value === "string" ? value : "";
}

export function DreamsPage() {
  const { projectId, refresh } = useWorkspace();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState<number>();
  const [confirmType, setConfirmType] = useState<string>();
  const [page, setPage] = useState(1);
  const listQuery = useQuery({
    queryKey: ["dreams", projectId, page],
    queryFn: () => getDreams(projectId!, { page, pageSize: 20 }),
    enabled: Boolean(projectId),
  });
  const rows = listQuery.data?.items ?? [];
  const selected = selectedId ?? rows[0]?.id;

  useEffect(() => {
    setSelectedId(undefined);
    setPage(1);
  }, [projectId]);

  const detailQuery = useQuery({
    queryKey: ["dream-detail", selected],
    queryFn: () => getDreamDetail(selected!),
    enabled: Boolean(selected),
  });
  const runMutation = useMutation({
    mutationFn: (type: string) => runDream(projectId!, type),
    onMutate: (type) => {
      toast.loading(type === "incremental" ? "正在整理新变更…" : "正在重新整理…", {
        id: "dream-run",
        description: "模型处理可能需要几分钟，请勿重复提交。",
      });
    },
    onSuccess: () => {
      refresh();
      toast.success("整理任务已创建", {
        id: "dream-run",
        description: "可在任务面板查看进度和预计完成时间",
        action: { label: "查看任务", onClick: () => navigate("/jobs") },
      });
    },
    onError: (error: Error) => toast.error(error.message, { id: "dream-run" }),
  });
  const revertMutation = useMutation({
    mutationFn: (id: number) => revertDreamChange(id, "撤回这次整理变更"),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["dream-detail", selected] });
      refresh();
      toast.success("已撤回");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <div className="grid gap-6">
      <PageHeader
        kicker="整理记录"
        title="整理历史"
        description="每次自动整理改了哪些记忆。点开一条看前后差异，不对可以撤回。"
        actions={
          <>
            <Button variant="secondary" disabled={!projectId || runMutation.isPending} onClick={() => setConfirmType("incremental")}>{runMutation.isPending ? "整理中…" : "整理新变更"}</Button>
            <Button disabled={!projectId || runMutation.isPending} onClick={() => setConfirmType("manual")}>{runMutation.isPending ? "整理中…" : "重新整理"}</Button>
          </>
        }
      />
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="grid content-start gap-2">
          {rows.map((item) => (
            <button
              key={item.id}
              className={`card p-4 text-left ${item.id === selected ? "bg-white/8" : "card-hover"}`}
              onClick={() => setSelectedId(item.id)}
            >
              <Badge tone={StatusTone(item.status)}>{showStatus(item.status)}</Badge>
              <p className="mt-2 text-sm font-medium">{showDreamType(item.dream_type)} #{item.id}</p>
              {item.error && <p className="mt-1 line-clamp-2 text-xs text-red-300">{item.error}</p>}
            </button>
          ))}
          <Pager page={page} pageSize={20} total={listQuery.data?.total ?? 0} onPage={setPage} />
          {!rows.length && <Empty text="还没有整理记录。" />}
        </div>
        <Card className="p-6">
          {!detailQuery.data && <Empty text="选择一次整理，查看它改了哪些经验。" />}
          {detailQuery.data && (
            <div className="grid gap-4">
              <div>
                <h3 className="text-lg font-medium">{showDreamType(detailQuery.data.dream_type)} #{detailQuery.data.id}</h3>
                <p className="mt-1 text-xs text-faint">{detailQuery.data.model} · {detailQuery.data.prompt_version} · {detailQuery.data.duration_ms}ms</p>
                {detailQuery.data.error && <p className="mt-2 text-sm text-red-300">{detailQuery.data.error}</p>}
              </div>
              {detailQuery.data.changes.map((change) => {
                const title = asText(change.after?.title) || asText(change.before?.title) || `记忆 #${change.memory_id}`;
                const problem = asText(change.after?.problem) || asText(change.before?.problem);
                return (
                  <article key={change.id} className="rounded-2xl bg-white/5 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <strong className="text-sm font-medium">{change.action} · {title}</strong>
                      <Badge tone={change.status === "reverted" ? "bad" : "neutral"}>{change.status === "reverted" ? "已撤回" : "已应用"}</Badge>
                    </div>
                    {problem && <p className="mt-2 text-sm text-mute">{problem}</p>}
                    <p className="mt-2 text-xs text-faint">{change.reason}</p>
                    <div className="mt-2 flex flex-wrap gap-3">
                      {change.memory_id && (
                        <Button variant="ghost" className="px-0" onClick={() => navigate(`/memories?id=${change.memory_id}`)}>查看记忆</Button>
                      )}
                      {change.status !== "reverted" && (
                        <Button variant="ghost" className="px-0" onClick={() => revertMutation.mutate(change.id)}>撤回</Button>
                      )}
                    </div>
                  </article>
                );
              })}
              {!detailQuery.data.changes.length && <Empty text="这次整理没有写出变更集。" />}
            </div>
          )}
        </Card>
      </div>
      <Dialog
        open={Boolean(confirmType)}
        title={confirmType === "incremental" ? "整理新变更？" : "重新整理？"}
        confirmLabel="开始"
        onClose={() => setConfirmType(undefined)}
        onConfirm={() => {
          if (confirmType) runMutation.mutate(confirmType);
          setConfirmType(undefined);
        }}
      >
        {confirmType === "incremental"
          ? "只处理新变更对应的主题，生成待审草稿，不会直接覆盖已启用经验。"
          : "对现有主题再合成一稿，结果进入变更集，已人工改过的字段不会被覆盖。"}
      </Dialog>
    </div>
  );
}
