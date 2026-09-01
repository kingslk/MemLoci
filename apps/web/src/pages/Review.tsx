import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { batchCorrectMemories, correctMemory, getMemories, getMemoryDetail, getTopics } from "../api";
import { MemoryPanel, memorySummary } from "../components/MemoryPanel";
import { Badge, Button, Card, Empty, Input, PageHeader, Pager, Select, StatusTone } from "../components/ui";
import { showStatus } from "../lib/labels";
import { useWorkspace } from "../workspace";

export function ReviewPage() {
  const { projectId, repositories, refresh } = useWorkspace();
  const client = useQueryClient();
  const [selectedId, setSelectedId] = useState<number>();
  const [checked, setChecked] = useState<number[]>([]);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [repositoryId, setRepositoryId] = useState("");
  const [topicId, setTopicId] = useState("");
  const listQuery = useQuery({
    queryKey: ["memories", projectId, "review", page, q, repositoryId, topicId],
    queryFn: () =>
      getMemories(projectId!, {
        status: "review",
        page,
        pageSize: 20,
        q: q || undefined,
        repositoryId: repositoryId ? Number(repositoryId) : undefined,
        topicId: topicId ? Number(topicId) : undefined,
      }),
    enabled: Boolean(projectId),
  });
  const topicsQuery = useQuery({
    queryKey: ["topics", projectId],
    queryFn: () => getTopics(projectId!),
    enabled: Boolean(projectId),
  });
  const queue = listQuery.data?.items ?? [];
  const selected = selectedId && queue.some((item) => item.id === selectedId) ? selectedId : queue[0]?.id;
  const allChecked = queue.length > 0 && queue.every((item) => checked.includes(item.id));

  useEffect(() => {
    setSelectedId(undefined);
    setChecked([]);
    setPage(1);
    setQ("");
    setRepositoryId("");
    setTopicId("");
  }, [projectId]);

  const detailQuery = useQuery({
    queryKey: ["memory-detail", selected],
    queryFn: () => getMemoryDetail(selected!),
    enabled: Boolean(selected),
  });
  const saveMutation = useMutation({
    mutationFn: (payload: Parameters<typeof correctMemory>[1]) => correctMemory(selected!, payload),
    onSuccess: (_, payload) => {
      const remaining = queue.filter((item) => item.id !== selected);
      setSelectedId(remaining[0]?.id);
      setChecked((ids) => ids.filter((id) => id !== selected));
      refresh();
      client.invalidateQueries({ queryKey: ["memories", projectId] });
      client.invalidateQueries({ queryKey: ["memory-detail", selected] });
      const left = remaining.length;
      toast.success(payload.status === "rejected" ? "已否决" : "已保存", {
        description: left ? `还剩 ${left} 条待审` : "待审已清空",
      });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  const batchMutation = useMutation({
    mutationFn: (payload: { status: string; reason: string; memory_ids: number[] }) =>
      batchCorrectMemories(payload),
    onSuccess: (data, payload) => {
      const ok = data.results.filter((item) => item.ok).length;
      const failed = data.results.filter((item) => !item.ok);
      refresh();
      client.invalidateQueries({ queryKey: ["memories", projectId] });
      setChecked([]);
      toast.success(`已处理 ${ok} 条`, {
        description: failed.length
          ? `${failed.length} 条跳过：${failed[0].error}`
          : payload.status === "rejected"
            ? "已否决"
            : "已更新状态",
      });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const runBatch = (status: string, reason: string) => {
    const ids =
      status === "active"
        ? queue.filter((item) => checked.includes(item.id) && (item.evidence_count ?? 0) > 0).map((item) => item.id)
        : checked;
    const skipped = status === "active" ? checked.length - ids.length : 0;
    if (!ids.length) {
      toast.error(skipped ? "选中的条都没有证据，不能启用" : "先勾选要处理的条");
      return;
    }
    if (skipped) toast(`${skipped} 条没有证据，已跳过`);
    batchMutation.mutate({ memory_ids: ids, status, reason });
  };

  return (
    <div className="grid gap-6">
      <PageHeader kicker="待审" title="审核记忆" description="一条条看完再启用。启用后 Agent 才会用到。没有证据的不能启用。" />
      <div className="grid gap-2 md:grid-cols-3">
        <Input placeholder="搜索标题或问题" value={q} onChange={(event) => { setQ(event.target.value); setPage(1); }} />
        <Select value={repositoryId} onChange={(event) => { setRepositoryId(event.target.value); setPage(1); }}>
          <option value="">全部仓库</option>
          {repositories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </Select>
        <Select value={topicId} onChange={(event) => { setTopicId(event.target.value); setPage(1); }}>
          <option value="">全部主题</option>
          {(topicsQuery.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </Select>
      </div>
      {checked.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-mute">
          <span>已选 {checked.length} 条</span>
          <Button variant="secondary" disabled={batchMutation.isPending} onClick={() => runBatch("tentative", "批量试用")}>批量试用</Button>
          <Button disabled={batchMutation.isPending} onClick={() => runBatch("active", "批量启用")}>批量启用</Button>
          <Button variant="danger" disabled={batchMutation.isPending} onClick={() => runBatch("rejected", "批量否决")}>批量否决</Button>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="grid max-h-[72vh] content-start gap-2 overflow-auto pr-1">
          {queue.length > 0 && (
            <label className="flex items-center gap-2 px-1 text-xs text-faint">
              <input
                type="checkbox"
                checked={allChecked}
                onChange={(event) => setChecked(event.target.checked ? queue.map((item) => item.id) : [])}
              />
              本页全选
            </label>
          )}
          {queue.map((item) => (
            <div
              key={item.id}
              className={`card flex gap-3 p-4 text-left ${item.id === selected ? "bg-white/8" : "card-hover"}`}
            >
              <input
                type="checkbox"
                className="mt-1"
                checked={checked.includes(item.id)}
                onChange={(event) => {
                  setChecked((ids) =>
                    event.target.checked ? [...ids, item.id] : ids.filter((id) => id !== item.id),
                  );
                }}
              />
              <button className="min-w-0 flex-1 text-left" onClick={() => setSelectedId(item.id)}>
                <div className="flex items-center justify-between gap-2">
                  <Badge tone={StatusTone(item.status)}>{showStatus(item.status)}</Badge>
                  <span className="text-[11px] text-faint">{item.evidence_count ?? 0} 条证据</span>
                </div>
                <p className="mt-2 text-sm font-medium">{item.title}</p>
                <p className="mt-1 line-clamp-2 text-xs text-faint">{memorySummary(item)}</p>
              </button>
            </div>
          ))}
          <Pager page={page} pageSize={20} total={listQuery.data?.total ?? 0} onPage={setPage} />
          {!listQuery.isLoading && !queue.length && <Empty text="没有待审。同步完成后，去整理记录跑一次「整理新变更」。" />}
        </div>
        <Card className="p-6">
          <MemoryPanel
            detail={detailQuery.data}
            onSave={(payload) => saveMutation.mutate(payload)}
            saving={saveMutation.isPending}
            compactActions
          />
        </Card>
      </div>
    </div>
  );
}
