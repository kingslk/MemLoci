import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getEvidence, getEvidenceDetail } from "../api";
import { Badge, Card, Empty, PageHeader, Pager, Select } from "../components/ui";
import { useWorkspace } from "../workspace";

function filePath(file: string | { path: string }) {
  return typeof file === "string" ? file : file.path;
}

export function EvidencePage() {
  const { projectId, repositories } = useWorkspace();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const requestedId = params.get("id") ? Number(params.get("id")) : undefined;
  const [selectedId, setSelectedId] = useState<number | undefined>(requestedId);
  const [repositoryId, setRepositoryId] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    setSelectedId(requestedId);
  }, [requestedId]);

  useEffect(() => {
    setRepositoryId("");
    setPage(1);
  }, [projectId]);

  const listQuery = useQuery({
    queryKey: ["evidence", projectId, repositoryId, page],
    queryFn: () =>
      getEvidence(projectId!, {
        repositoryId: repositoryId ? Number(repositoryId) : undefined,
        page,
        pageSize: 20,
      }),
    enabled: Boolean(projectId),
  });
  const rows = listQuery.data?.items ?? [];
  const selected = selectedId ?? rows[0]?.id;
  const detailQuery = useQuery({
    queryKey: ["evidence-detail", selected],
    queryFn: () => getEvidenceDetail(selected!),
    enabled: Boolean(selected),
  });
  const detail = detailQuery.data;

  return (
    <div className="grid gap-6">
      <PageHeader kicker="证据" title="变更证据" description="提交和合并请求里抽出来的事实。点开看 diff、改过的文件，以及据此写出的记忆。" />
      <Select className="max-w-xs" value={repositoryId} onChange={(event) => { setRepositoryId(event.target.value); setPage(1); }}>
        <option value="">全部仓库</option>
        {repositories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </Select>
      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
        <Card className="min-w-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full table-fixed text-left text-sm">
              <thead className="text-xs text-faint">
                <tr>
                  <th className="w-[45%] px-4 py-3 font-medium">标题</th>
                  <th className="w-[20%] px-4 py-3 font-medium">来源</th>
                  <th className="w-[35%] px-4 py-3 font-medium">摘要</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr
                    key={item.id}
                    className={`cursor-pointer border-t border-white/6 ${item.id === selected ? "bg-white/8" : "hover:bg-white/4"}`}
                    onClick={() => {
                      setSelectedId(item.id);
                      setParams({ id: String(item.id) });
                    }}
                  >
                    <td className="truncate px-4 py-3">{item.title}</td>
                    <td className="truncate px-4 py-3 text-faint">{item.source_type}</td>
                    <td className="truncate px-4 py-3 text-faint">{item.summary || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={20} total={listQuery.data?.total ?? 0} onPage={setPage} />
          {!listQuery.isLoading && !rows.length && <Empty text="还没有证据。先同步仓库或跑初始化。" />}
        </Card>
        <Card className="min-w-0 overflow-hidden p-6">
          {!detail && <Empty text="选择一条证据。" />}
          {detail && (
            <div className="grid min-w-0 gap-4">
              <div className="min-w-0">
                <p className="text-xs text-faint">#{detail.id} · {detail.source_type}</p>
                <h2 className="mt-1 break-words text-xl font-medium tracking-tight">{detail.title}</h2>
                <p className="mt-2 break-words text-sm text-mute">{detail.summary || "没有摘要。"}</p>
              </div>
              {!!detail.files.length && (
                <section className="min-w-0">
                  <h3 className="text-sm font-medium">变更文件</h3>
                  <ul className="mt-2 grid gap-1 text-xs text-faint">
                    {detail.files.slice(0, 40).map((file) => (
                      <li key={filePath(file)} className="break-all">{filePath(file)}</li>
                    ))}
                  </ul>
                </section>
              )}
              {detail.diff && (
                <section className="min-w-0">
                  <h3 className="text-sm font-medium">Diff</h3>
                  <pre className="mt-2 max-h-80 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-2xl bg-black/40 p-3 text-[11px] leading-5 text-zinc-300">{detail.diff.slice(0, 8000)}</pre>
                </section>
              )}
              <section className="min-w-0">
                <h3 className="text-sm font-medium">抽出来的经验</h3>
                <div className="mt-2 grid gap-2">
                  {detail.memories.map((item) => (
                    <button key={item.id} className="rounded-2xl bg-white/5 px-3 py-2 text-left" onClick={() => navigate(`/memories?id=${item.id}`)}>
                      <Badge>{item.status}</Badge>
                      <p className="mt-1 break-words text-sm">{item.title}</p>
                    </button>
                  ))}
                  {!detail.memories.length && <p className="text-sm text-faint">还没有关联经验。</p>}
                </div>
              </section>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
