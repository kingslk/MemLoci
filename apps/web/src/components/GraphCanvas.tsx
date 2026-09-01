import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { useEffect, useRef, useState } from "react";
import Sigma from "sigma";
import { GraphData, GraphNode } from "../api";

const colors: Record<string, string> = {
  memory: "#f4f4f5",
  topic: "#60a5fa",
  evidence: "#fbbf24",
  code: "#34d399",
};

export function GraphCanvas({
  data,
  onSelect,
}: {
  data: GraphData;
  onSelect: (node: GraphNode | null) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<Sigma | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [selected, setSelected] = useState<GraphNode | null>(null);

  useEffect(() => {
    if (!containerRef.current || !data.nodes.length) return;
    const graph = new Graph({ multi: true, type: "directed" });
    data.nodes.forEach((node, index) => {
      const angle = (index / Math.max(data.nodes.length, 1)) * Math.PI * 2;
      graph.addNode(node.id, {
        ...node,
        x: Math.cos(angle) + Math.random() * 0.2,
        y: Math.sin(angle) + Math.random() * 0.2,
        size: node.subtype === "directory" ? 10 : node.kind === "topic" ? 9 : 6,
        color: colors[node.kind] ?? "#71717a",
      });
    });
    data.edges.forEach((edge, index) => {
      if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) return;
      graph.addEdgeWithKey(`${edge.id}:${index}`, edge.source, edge.target, {
        color: "#3f3f46",
        size: 1,
        relationType: edge.type,
      });
    });
    forceAtlas2.assign(graph, {
      iterations: 90,
      settings: { ...forceAtlas2.inferSettings(graph), gravity: 1.2, scalingRatio: 8 },
    });

    let hovered: string | null = null;
    let picked: string | null = null;
    const renderer = new Sigma(graph, containerRef.current, {
      allowInvalidContainer: true,
      labelColor: { color: "#a1a1aa" },
      labelDensity: 0.08,
      defaultEdgeColor: "#3f3f46",
      minCameraRatio: 0.15,
      maxCameraRatio: 5,
      nodeReducer: (node, attributes) => {
        const focus = hovered ?? picked;
        if (!focus) return attributes;
        if (node === focus) return { ...attributes, highlighted: true, forceLabel: true, size: attributes.size * 1.3 };
        if (graph.areNeighbors(node, focus)) return { ...attributes, forceLabel: true };
        return { ...attributes, color: "#27272a", label: "" };
      },
    });
    rendererRef.current = renderer;
    renderer.on("clickNode", ({ node }) => {
      picked = node;
      const attributes = graph.getNodeAttributes(node);
      const next = {
        id: node,
        label: String(attributes.label),
        kind: String(attributes.kind),
        subtype: attributes.subtype ? String(attributes.subtype) : undefined,
        path: attributes.path ? String(attributes.path) : undefined,
        summary: attributes.summary ? String(attributes.summary) : undefined,
        status: attributes.status ? String(attributes.status) : undefined,
      };
      setSelected(next);
      onSelectRef.current(next);
      renderer.refresh();
    });
    renderer.on("clickStage", () => {
      picked = null;
      setSelected(null);
      onSelectRef.current(null);
      renderer.refresh();
    });
    renderer.on("enterNode", ({ node }) => {
      hovered = node;
      renderer.refresh();
    });
    renderer.on("leaveNode", () => {
      hovered = null;
      renderer.refresh();
    });
    return () => {
      renderer.kill();
      rendererRef.current = null;
    };
  }, [data]);

  return (
    <div className="relative min-w-0 overflow-hidden rounded-xl border border-line bg-zinc-950">
      <div className="absolute right-3 top-3 z-10 flex gap-1">
        <button className="rounded-md border border-line bg-surface px-2 py-1 text-xs" onClick={() => rendererRef.current?.getCamera().animatedZoom()}>＋</button>
        <button className="rounded-md border border-line bg-surface px-2 py-1 text-xs" onClick={() => rendererRef.current?.getCamera().animatedUnzoom()}>－</button>
        <button className="rounded-md border border-line bg-surface px-2 py-1 text-xs" onClick={() => rendererRef.current?.getCamera().animatedReset()}>适配</button>
      </div>
      <div ref={containerRef} className="graph-canvas" />
      <div className="pointer-events-none absolute bottom-3 left-3 flex gap-3 text-[11px] text-muted">
        <span>经验</span><span className="text-blue-400">主题</span><span className="text-amber-300">证据</span><span className="text-emerald-300">代码</span>
      </div>
      {selected && (
        <div className="absolute left-3 top-3 max-w-xs rounded-lg border border-line bg-surface/95 px-3 py-2">
          <strong className="block truncate text-sm">{selected.label}</strong>
          <span className="text-[11px] text-dim">{selected.kind}{selected.path ? ` · ${selected.path}` : ""}</span>
        </div>
      )}
    </div>
  );
}
