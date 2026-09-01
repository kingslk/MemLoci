import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { correctMemory, getMemories, getMemoryDetail, getTopics } from "../api";
import { MemoryPanel } from "../components/MemoryPanel";
import { Badge, Card, Empty, Input, PageHeader, Pager, Select, StatusTone } from "../components/ui";
import { showStatus } from "../lib/labels";
import { useWorkspace } from "../workspace";

export function MemoriesPage() {
  const { projectId, repositories } = useWorkspace();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const requestedId = params.get("id") ? Number(params.get("id")) : undefined;
  const [selectedId, setSelectedId] = useState<number | undefined>(requestedId);
  const [status, setStatus] = useState(requestedId ? "all" : "library");
  const [repositoryId, setRepositoryId] = useState("");
  const [topicId, setTopicId] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    setSelectedId(requestedId);
  }, [requestedId]);

  useEffect(() => {
    setRepositoryId("");
    setTopicId("");
    setQ("");
    setPage(1);
  }, [projectId]);

  const topicsQuery = useQuery({
    queryKey: ["topics", projectId],
    queryFn: () => getTopics(projectId!),
    enabled: Boolean(projectId),
  });
  const listQuery = useQuery({
    queryKey: ["memories", projectId, status, repositoryId, topicId, q, page],
    queryFn: () =>
      getMemories(projectId!, {
        repositoryId: repositoryId ? Number(repositoryId) : undefined,
        status: status === "all" ? undefined : status,
        topicId: topicId ? Number(topicId) : undefined,
        q: q || undefined,
        page,
        pageSize: 20,
      }),
    enabled: Boolean(projectId),
  });
  const rows = listQuery.data?.items ?? [];
  const detailQuery = useQuery({
    queryKey: ["memory-detail", selectedId],
    queryFn: () => getMemoryDetail(selectedId!),
    enabled: Boolean(selectedId),
  });
  const saveMutation = useMutation({
    mutationFn: (payload: Parameters<typeof correctMemory>[1]) => correctMemory(selectedId!, payload),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["memories", projectId] });
      client.invalidateQueries({ queryKey: ["memory-detail", selectedId] });
      toast.success("已保存");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <div className="grid gap-6">
      <PageHeader kicker="记忆库" title="已留下的经验" description="点开一条看做法、证据和 diff。默认只看已启用和归档。" />
      <div className="grid gap-2 md:grid-cols-4">
        <Input placeholder="搜索标题或要解决的问题" value={q} onChange={(event) => { setQ(event.target.value); setPage(1); }} />
        <Select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
          <option value="library">已启用 / 过时 / 归档</option>
          <option value="all">全部</option>
          <option value="active">已启用</option>
          <option value="deprecated">已过时</option>
          <option value="archived">已归档</option>
          <option value="review">待审 / 试用</option>
          <option value="candidate">待审</option>
          <option value="tentative">试用中</option>
        </Select>
        <Select value={repositoryId} onChange={(event) => { setRepositoryId(event.target.value); setPage(1); }}>
          <option value="">全部仓库</option>
          {repositories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </Select>
        <Select value={topicId} onChange={(event) => { setTopicId(event.target.value); setPage(1); }}>
          <option value="">全部主题</option>
          {(topicsQuery.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </Select>
      </div>
      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        <Card className="min-w-0 overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-faint">
              <tr>
                <th className="px-4 py-3 font-medium">标题</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">仓库</th>
                <th className="px-4 py-3 font-medium">证据</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr
                  key={item.id}
                  className={`cursor-pointer border-t border-white/6 ${item.id === selectedId ? "bg-white/8" : "hover:bg-white/4"}`}
                  onClick={() => {
                    setSelectedId(item.id);
                    setParams({ id: String(item.id) });
                  }}
                >
                  <td className="px-4 py-3">{item.title}</td>
                  <td className="px-4 py-3"><Badge tone={StatusTone(item.status)}>{showStatus(item.status)}</Badge></td>
                  <td className="px-4 py-3 text-faint">{item.repository_name || "项目级"}</td>
                  <td className="px-4 py-3 text-faint">{item.evidence_count ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager page={page} pageSize={20} total={listQuery.data?.total ?? 0} onPage={setPage} />
          {!listQuery.isLoading && !rows.length && <Empty text="没有匹配的经验。试试「全部」，或先去待审。" />}
        </Card>
        <Card className="min-w-0 overflow-hidden p-6">
          <MemoryPanel
            detail={detailQuery.data}
            onSave={(payload) => saveMutation.mutate(payload)}
            saving={saveMutation.isPending}
          />
        </Card>
      </div>
    </div>
  );
}
