import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getGraph, GraphNode } from "../api";
import { GraphCanvas } from "../components/GraphCanvas";
import { Button, Card, Empty, Input, PageHeader, Select } from "../components/ui";
import { useWorkspace } from "../workspace";

type Kind = "memory" | "code" | "neighborhood";

const kindHint: Record<Kind, string> = {
  memory: "单击选中一条经验或主题。展开关联看证据和文件，打开详情进记忆库。",
  code: "左边是当前目录。点目录或文件只是选中；进入目录或看关系要用按钮。",
  neighborhood: "选中经验周围的主题、证据和文件。单击节点只是选中。",
};

const edgeLabel: Record<string, string> = {
  belongs_to: "属于",
  touches: "改过",
  contains: "包含",
  imports: "导入",
  calls: "调用",
  derived_from: "来自",
  supports: "支持",
};

export function GraphPage() {
  const { projectId, repositories } = useWorkspace();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const kind = (["memory", "code", "neighborhood"].includes(params.get("kind") || "")
    ? params.get("kind")
    : "memory") as Kind;
  const repositoryId = params.get("repo") ?? "";
  const focus = params.get("focus") ?? undefined;
  const prefix = params.get("prefix") ?? "";
  const fileId = params.get("file") ? Number(params.get("file")) : undefined;
  const query = params.get("q") ?? "";
  const selectedId = params.get("node") ?? undefined;

  const setGraph = (next: Record<string, string | undefined>) => {
    const merged = new URLSearchParams(params);
    Object.entries(next).forEach(([key, value]) => {
      if (value) merged.set(key, value);
      else merged.delete(key);
    });
    setParams(merged);
  };

  const graphQuery = useQuery({
    queryKey: ["graph", projectId, kind, repositoryId, focus, prefix],
    queryFn: () =>
      getGraph(projectId!, kind === "neighborhood" ? "neighborhood" : kind, {
        repositoryId: repositoryId ? Number(repositoryId) : undefined,
        focus: kind === "neighborhood" ? focus : undefined,
        prefix: kind === "code" ? prefix : undefined,
      }),
    enabled: Boolean(projectId) && (kind !== "code" || Boolean(repositoryId)),
  });
  const fileQuery = useQuery({
    queryKey: ["graph-file", projectId, repositoryId, fileId],
    queryFn: () =>
      getGraph(projectId!, "code", {
        repositoryId: Number(repositoryId),
        fileId,
      }),
    enabled: Boolean(projectId && repositoryId && fileId),
  });
  const data = graphQuery.data;
  const filtered = data
    ? {
        ...data,
        nodes: query
          ? data.nodes.filter((node) => `${node.label} ${node.path ?? ""}`.toLowerCase().includes(query.toLowerCase()))
          : data.nodes,
        edges: data.edges,
      }
    : undefined;
  const visible = filtered
    ? {
        nodes: filtered.nodes,
        edges: filtered.edges.filter(
          (edge) => filtered.nodes.some((node) => node.id === edge.source) && filtered.nodes.some((node) => node.id === edge.target),
        ),
      }
    : undefined;
  const meta = data?.meta;
  const parent = meta?.parent ?? (prefix.includes("/") ? prefix.slice(0, prefix.lastIndexOf("/")) : "");
  const selected = useMemo(() => {
    const pool = [...(visible?.nodes ?? []), ...(fileQuery.data?.nodes ?? [])];
    return pool.find((node) => node.id === selectedId) ?? null;
  }, [visible, fileQuery.data, selectedId]);
  const treeNodes = (visible?.nodes ?? []).filter((node) => node.id !== `dir:${repositoryId}:${prefix}` && node.id !== `dir:${repositoryId}:`);
  const crumbs = ["仓库根", ...prefix.split("/").filter(Boolean)];

  const onSelect = (node: GraphNode | null) => {
    if (!node) {
      setGraph({ node: undefined });
      return;
    }
    setGraph({
      node: node.id,
      focus: node.kind === "memory" ? node.id : focus,
    });
  };

  return (
    <div className="grid gap-6">
      <PageHeader kicker="图谱" title="从经验出发" description="单击只是选中。经验可以展开关联或打开详情；代码按目录浏览，选中文件再看导入和调用。" />
      <div className="flex flex-wrap gap-2">
        {([
          ["memory", "经验关系"],
          ["code", "代码结构"],
          ["neighborhood", "关联展开"],
        ] as Array<[Kind, string]>).map(([key, label]) => (
          <button
            key={key}
            className={`rounded-full px-4 py-2 text-sm font-medium ${kind === key ? "bg-white text-black" : "bg-white/5 text-mute hover:bg-white/8"}`}
            onClick={() => setGraph({ kind: key, node: undefined, file: undefined })}
          >
            {label}
          </button>
        ))}
        <Select
          className="w-40"
          value={repositoryId}
          onChange={(event) => setGraph({ repo: event.target.value || undefined, prefix: undefined, file: undefined, node: undefined })}
        >
          <option value="">{kind === "code" ? "选择仓库" : "全部仓库"}</option>
          {repositories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </Select>
        <Input
          className="max-w-xs"
          placeholder="搜索节点"
          value={query}
          onChange={(event) => setGraph({ q: event.target.value || undefined })}
        />
      </div>
      <p className="text-sm text-mute">{kindHint[kind]}</p>
      {kind === "code" && repositoryId && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-faint">
          <span>
            {crumbs.map((item, index) => (
              <button
                key={`${item}-${index}`}
                className="hover:text-ink"
                onClick={() =>
                  setGraph({
                    prefix: crumbs.slice(1, index + 1).join("/") || undefined,
                    file: undefined,
                    node: undefined,
                  })
                }
              >
                {index ? " / " : ""}
                {item}
              </button>
            ))}
          </span>
          {prefix && (
            <Button variant="ghost" className="px-0" onClick={() => setGraph({ prefix: parent || undefined, file: undefined, node: undefined })}>
              返回上级
            </Button>
          )}
          {meta?.file_total != null && (
            <span>
              仓库 {meta.file_total} 个文件 · 本层 {meta.dir_count ?? 0} 个目录 / {meta.file_count ?? 0} 个文件
              {meta.truncated ? "（文件已截断，请再点目录）" : ""}
            </span>
          )}
        </div>
      )}
      {kind === "neighborhood" && (
        <div className="text-xs text-faint">
          当前位置：{focus ? `经验 ${focus}` : "还没选经验"}
          {focus && (
            <Button variant="ghost" className="ml-2 px-0" onClick={() => setGraph({ kind: "memory", focus: undefined })}>
              回到经验关系
            </Button>
          )}
        </div>
      )}
      {kind === "code" && !repositoryId && <Empty text="代码结构需要先选仓库。" />}
      {kind === "neighborhood" && !focus && <p className="text-sm text-mute">先在经验关系里点一条经验，再点「展开关联」。</p>}
      {graphQuery.isLoading && <p className="text-sm text-mute">加载图谱…</p>}
      {kind === "code" && repositoryId && treeNodes.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
          <Card className="grid max-h-[62vh] content-start gap-1 overflow-auto p-2">
            {treeNodes.map((node) => (
              <button
                key={node.id}
                className={`rounded-lg px-3 py-2 text-left text-sm ${selectedId === node.id ? "bg-white/8" : "hover:bg-white/4"}`}
                onClick={() => onSelect(node)}
                onDoubleClick={() => {
                  if (node.subtype === "directory" && node.path) {
                    setGraph({ prefix: node.path, node: undefined, file: undefined });
                  }
                  if (node.subtype === "file") {
                    setGraph({ file: node.id.replace("code:", ""), node: node.id });
                  }
                }}
              >
                <span className="text-faint">{node.subtype === "directory" ? "目录" : "文件"} · </span>
                {node.label}
              </button>
            ))}
          </Card>
          <div className="grid gap-3">
            {fileQuery.data && fileQuery.data.nodes.length > 0 ? (
              <GraphCanvas data={fileQuery.data} onSelect={onSelect} />
            ) : (
              <Empty text={selected?.subtype === "file" ? "这个文件还没有符号关系。双击或点「查看关系」再试。" : "选中一个文件后查看导入和调用。"} />
            )}
          </div>
        </div>
      )}
      {kind !== "code" && visible && visible.nodes.length > 0 && (
        <GraphCanvas data={visible} onSelect={onSelect} />
      )}
      {visible && !visible.nodes.length && !graphQuery.isLoading && kind !== "code" && (
        <Empty text="还没有可展示的经验。先审过几条再来看图。" />
      )}
      {selected && (
        <Card className="flex items-center justify-between px-4 py-3">
          <div>
            <p className="text-sm font-medium">{selected.label}</p>
            <p className="text-xs text-faint">
              {selected.kind === "memory" && "经验 · 可展开关联或打开记忆库"}
              {selected.kind === "topic" && "主题 · 多条经验的归类"}
              {selected.kind === "evidence" && "证据 · 一次真实变更"}
              {selected.kind === "code" && (
                selected.subtype === "directory"
                  ? `目录 · ${selected.path || ""}`
                  : selected.subtype === "missing"
                    ? `路径已变 · ${selected.path || ""}`
                    : selected.subtype === "symbol"
                      ? `符号 · ${selected.summary || ""}`
                      : `文件 · ${selected.path || ""}`
              )}
              {!["memory", "topic", "evidence", "code"].includes(selected.kind) && (selected.summary || selected.path || selected.kind)}
            </p>
            {selected.summary && <p className="mt-1 text-xs text-mute">{selected.summary}</p>}
            {kind !== "code" && visible && (
              <p className="mt-1 text-[11px] text-faint">
                连线：
                {[...new Set(visible.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id).map((edge) => edgeLabel[edge.type] || edge.type))].join(" / ") || "无"}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            {selected.kind === "memory" && (
              <>
                <Button variant="secondary" onClick={() => setGraph({ kind: "neighborhood", focus: selected.id })}>展开关联</Button>
                <Button onClick={() => navigate(`/memories?id=${selected.id.replace("memory:", "")}`)}>打开详情</Button>
              </>
            )}
            {selected.kind === "evidence" && (
              <Button onClick={() => navigate(`/evidence?id=${selected.id.replace("evidence:", "")}`)}>打开证据</Button>
            )}
            {kind === "code" && selected.subtype === "directory" && selected.path && (
              <Button onClick={() => setGraph({ prefix: selected.path, node: undefined, file: undefined })}>进入目录</Button>
            )}
            {kind === "code" && selected.subtype === "file" && (
              <Button onClick={() => setGraph({ file: selected.id.replace("code:", ""), node: selected.id })}>查看关系</Button>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
