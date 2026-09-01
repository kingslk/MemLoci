import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { clearQueryLogs, getQueryLogs, QueryLog } from "../api";
import { Badge, Button, Card, Dialog, Empty, PageHeader, Pager, Select, StatusTone } from "../components/ui";
import { useWorkspace } from "../workspace";

const modeLabel: Record<string, string> = {
  active: "回了已启用的记忆",
  tentative_fallback: "只用了试用稿",
  empty: "什么都没回",
};

const emptyReasonLabel: Record<string, string> = {
  no_keyword_path: "提问和库里的经验对不上用词。",
  below_floor: "有一点点相关，但不够强，所以没收进来。",
  token_budget: "内容太长，超出这次的长度限制。",
};

function scoreRows(log: QueryLog) {
  const rows = log.output_summary?.results;
  return Array.isArray(rows) ? rows : [];
}

function repoBreakdown(log: QueryLog) {
  const rows = log.results_by_repository?.length
    ? log.results_by_repository
    : log.output_summary?.results_by_repository;
  return Array.isArray(rows) ? rows : [];
}

function percent(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${Math.round(number * 100)}%`;
}

function whyReturned(row: Record<string, unknown>) {
  const keyword = Number(row.keyword) || 0;
  if (keyword >= 0.25) return "提问和这条经验用词对得上";
  if (keyword > 0) return "只有少量用词重合";
  return "用词几乎对不上";
}

function emptyReason(log: QueryLog) {
  const cutoff = log.output_summary?.cutoff;
  if (!cutoff || typeof cutoff !== "object") return "";
  const key = String((cutoff as { empty_reason?: string }).empty_reason || "");
  return emptyReasonLabel[key] || "";
}

function coverageText(log: QueryLog) {
  const searched = log.searched_repository_count ?? log.output_summary?.searched_repository_count;
  const parts = repoBreakdown(log);
  if (!log.returned_count) {
    return searched ? `查了项目里 ${searched} 个仓库，没有够格的条。` : "没有够格的条。";
  }
  const detail = parts.map((item) => `${item.name} ${item.count} 条`).join("、");
  const head = searched
    ? `查了项目里 ${searched} 个仓库，回了 ${log.returned_count} 条`
    : `回了 ${log.returned_count} 条`;
  return detail ? `${head}：${detail}。Agent 应按做法汇总，不要只盯一个仓。` : `${head}。`;
}

export function QueryLogsPage() {
  const { projectId } = useWorkspace();
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const [recallMode, setRecallMode] = useState("");
  const [selectedId, setSelectedId] = useState<number>();
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    setPage(1);
    setSelectedId(undefined);
  }, [projectId, recallMode]);

  const listQuery = useQuery({
    queryKey: ["query-logs", projectId, page, recallMode],
    queryFn: () =>
      getQueryLogs(projectId!, {
        page,
        pageSize: 20,
        recallMode: recallMode || undefined,
      }),
    enabled: Boolean(projectId),
  });
  const rows = listQuery.data?.items ?? [];
  const selected = rows.find((item) => item.id === selectedId) ?? rows[0];
  const clearMutation = useMutation({
    mutationFn: () => clearQueryLogs(projectId!),
    onSuccess: (data) => {
      client.invalidateQueries({ queryKey: ["query-logs", projectId] });
      setSelectedId(undefined);
      toast.success(`已清空 ${data.deleted} 条检索记录`);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <div className="grid gap-6">
      <PageHeader
        kicker="召回日志"
        title="检索记录"
        description="一次提问会查项目下多个仓库。记下各仓回了几条，方便看 Agent 有没有材料可汇总。"
        actions={
          <Button
            variant="danger"
            disabled={!projectId || !listQuery.data?.total || clearMutation.isPending}
            onClick={() => setConfirmClear(true)}
          >
            清空本项目记录
          </Button>
        }
      />
      <Select className="max-w-xs" value={recallMode} onChange={(event) => setRecallMode(event.target.value)}>
        <option value="">全部结果</option>
        <option value="active">回了已启用的记忆</option>
        <option value="tentative_fallback">只用了试用稿</option>
        <option value="empty">什么都没回</option>
      </Select>
      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
        <Card className="min-w-0 overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-faint">
              <tr>
                <th className="px-4 py-3 font-medium">问了什么</th>
                <th className="px-4 py-3 font-medium">结果</th>
                <th className="px-4 py-3 font-medium">来自几个仓</th>
                <th className="px-4 py-3 font-medium">回了几条</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => {
                const repos = item.result_repository_count ?? repoBreakdown(item).length;
                return (
                  <tr
                    key={item.id}
                    className={`cursor-pointer border-t border-white/6 ${item.id === selected?.id ? "bg-white/8" : "hover:bg-white/4"}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <td className="max-w-xs truncate px-4 py-3">{item.task || "—"}</td>
                    <td className="px-4 py-3">
                      <Badge tone={item.recall_mode === "empty" ? "bad" : StatusTone("active")}>
                        {modeLabel[item.recall_mode] || item.recall_mode || "—"}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-faint">{item.returned_count ? `${repos || 1} 个` : "—"}</td>
                    <td className="px-4 py-3 text-faint">{item.returned_count}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Pager page={page} pageSize={20} total={listQuery.data?.total ?? 0} onPage={setPage} />
          {!listQuery.isLoading && !rows.length && <Empty text="还没有检索记录。Agent 调过 memory_context 后会出现在这里。" />}
        </Card>
        <Card className="grid min-w-0 gap-4 overflow-hidden p-6">
          {!selected && <Empty text="点左边一条看看详情" />}
          {selected && (
            <>
              <div>
                <p className="text-xs text-faint">
                  {selected.created_at ? new Date(selected.created_at).toLocaleString("zh-CN") : ""}
                  {selected.latency_ms ? ` · 用了 ${selected.latency_ms} 毫秒` : ""}
                </p>
                <p className="mt-2 break-words text-sm">{selected.task}</p>
              </div>
              <p className="break-words text-sm text-mute">{coverageText(selected)}</p>
              {selected.recall_mode === "empty" && emptyReason(selected) && (
                <p className="text-sm text-mute">{emptyReason(selected)}</p>
              )}
              <div className="grid gap-2">
                {scoreRows(selected).map((row) => (
                  <div key={String(row.id)} className="rounded-xl bg-white/5 px-3 py-2 text-sm">
                    <p className="break-words font-medium">#{String(row.id)} {String(row.title || "")}</p>
                    <p className="mt-1 text-xs text-faint">{String(row.repository_name || "项目级")}</p>
                    <p className="mt-1 text-xs text-mute">{whyReturned(row)}</p>
                    <p className="mt-1 text-xs text-faint">
                      用词重合 {percent(row.keyword)}
                      {row.distinctive != null ? ` · 独有用词 ${percent(row.distinctive)}` : ""}
                      {row.is_new === false ? " · 这轮对话里已经给过" : ""}
                    </p>
                  </div>
                ))}
                {!scoreRows(selected).length && <p className="text-sm text-faint">这次没有返回任何记忆。</p>}
              </div>
              {selected.output_summary?.hint && (
                <p className="text-xs text-mute">{String(selected.output_summary.hint)}</p>
              )}
            </>
          )}
        </Card>
      </div>
      <Dialog
        open={confirmClear}
        title="清空这个项目的检索记录？"
        confirmLabel="清空"
        onClose={() => setConfirmClear(false)}
        onConfirm={() => {
          clearMutation.mutate();
          setConfirmClear(false);
        }}
      >
        只删当前项目的检索记录，记忆和证据都不会动。删了就不能按这些提问复盘了。
      </Dialog>
    </div>
  );
}
