import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Memory, MemoryDetail } from "../api";
import {
  allowedTransitions,
  callableStatus,
  fieldHints,
  showStatus,
} from "../lib/labels";
import { Badge, Button, Field, Input, LineList, Select, StatusTone, Textarea } from "./ui";

type Draft = {
  title: string;
  status: string;
  confidence: string;
  problem: string;
  pattern: string[];
  do_not_copy: string[];
  apply_when: string[];
  do_not: string[];
  implementation: string;
};

const emptyDraft = (): Draft => ({
  title: "",
  status: "candidate",
  confidence: "0",
  problem: "",
  pattern: [],
  do_not_copy: [],
  apply_when: [],
  do_not: [],
  implementation: "",
});

export function MemoryPanel({
  detail,
  onSave,
  saving,
  compactActions,
}: {
  detail?: MemoryDetail;
  onSave: (payload: Parameters<typeof import("../api").correctMemory>[1]) => void;
  saving: boolean;
  compactActions?: boolean;
}) {
  const memory = detail?.memory;
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (!memory) return;
    setEditing(false);
    setReason("");
    setDraft({
      title: memory.title,
      status: memory.status,
      confidence: String(memory.confidence),
      problem: memory.problem,
      pattern: memory.pattern,
      do_not_copy: memory.do_not_copy,
      apply_when: memory.apply_when,
      do_not: memory.do_not,
      implementation: String(memory.implementation?.summary ?? ""),
    });
  }, [memory]);

  if (!memory) {
    return <div className="grid min-h-72 place-items-center text-sm text-faint">选择一条经验</div>;
  }

  const nextStatuses = [memory.status, ...(allowedTransitions[memory.status] ?? [])];
  const save = (overrides: Partial<Draft> = {}, nextReason = reason || "审核通过") => {
    const next = { ...draft, ...overrides };
    onSave({
      title: next.title,
      status: next.status,
      confidence: Number(next.confidence),
      problem: next.problem,
      pattern: next.pattern,
      do_not_copy: next.do_not_copy,
      apply_when: next.apply_when,
      do_not: next.do_not,
      implementation: next.implementation ? { summary: next.implementation } : undefined,
      reason: nextReason,
    });
  };

  return (
    <div className="grid gap-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs text-faint">#{memory.id} · v{memory.version}</p>
          <h2 className="mt-1 text-xl font-medium tracking-tight">{memory.title}</h2>
        </div>
        <Badge tone={StatusTone(memory.status)}>{showStatus(memory.status)}</Badge>
      </div>
      <div className="rounded-2xl bg-white/5 px-4 py-3 text-sm text-mute">
        {callableStatus.has(memory.status) ? "Agent 可以召回这条经验" : "当前不会被 Agent 召回"}
      </div>

      {!editing ? (
        <>
          <ReadBlock title="解决什么" hint={fieldHints.problem} items={[memory.problem || "还没写"]} />
          <ReadBlock title="怎么做" hint={fieldHints.pattern} items={memory.pattern} />
          <ReadBlock title="别照搬" hint={fieldHints.do_not_copy} items={memory.do_not_copy} />
          <ReadBlock title="什么时候用" hint={fieldHints.apply_when} items={memory.apply_when} />
          <ReadBlock title="什么时候别用" hint={fieldHints.do_not} items={memory.do_not} />
          {Boolean(memory.implementation && Object.keys(memory.implementation).length) && (
            <details className="rounded-2xl bg-white/5 px-4 py-3">
              <summary className="cursor-pointer text-sm font-medium">当时怎么做的</summary>
              <p className="mt-2 text-sm text-mute">{fieldHints.implementation}</p>
              <pre className="mt-2 overflow-auto text-xs text-zinc-300">{JSON.stringify(memory.implementation, null, 2)}</pre>
            </details>
          )}
        </>
      ) : (
        <div className="grid gap-4">
          <Field label="标题" hint={fieldHints.title}>
            <Input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
          </Field>
          <Field label="解决什么" hint={fieldHints.problem}>
            <Textarea rows={3} value={draft.problem} onChange={(event) => setDraft({ ...draft, problem: event.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="状态">
              <Select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value })}>
                {nextStatuses.map((item) => <option key={item} value={item}>{showStatus(item)}</option>)}
              </Select>
            </Field>
            <Field label="可信度">
              <Input type="number" min="0" max="1" step="0.01" value={draft.confidence} onChange={(event) => setDraft({ ...draft, confidence: event.target.value })} />
            </Field>
          </div>
          <Field label="怎么做" hint={fieldHints.pattern}>
            <LineList value={draft.pattern} onChange={(pattern) => setDraft({ ...draft, pattern })} />
          </Field>
          <Field label="别照搬" hint={fieldHints.do_not_copy}>
            <LineList value={draft.do_not_copy} onChange={(do_not_copy) => setDraft({ ...draft, do_not_copy })} />
          </Field>
          <Field label="什么时候用" hint={fieldHints.apply_when}>
            <LineList value={draft.apply_when} onChange={(apply_when) => setDraft({ ...draft, apply_when })} />
          </Field>
          <Field label="什么时候别用" hint={fieldHints.do_not}>
            <LineList value={draft.do_not} onChange={(do_not) => setDraft({ ...draft, do_not })} />
          </Field>
          <Field label="当时怎么做的" hint={fieldHints.implementation}>
            <Textarea rows={3} value={draft.implementation} onChange={(event) => setDraft({ ...draft, implementation: event.target.value })} />
          </Field>
          <Field label="原因">
            <Input value={reason} placeholder="改内容时写一句即可" onChange={(event) => setReason(event.target.value)} />
          </Field>
        </div>
      )}

      <section className="grid gap-2">
        <h3 className="text-sm font-semibold">事实证据</h3>
        {detail?.evidence.map((item) => (
          <details key={item.id} className="rounded-2xl bg-white/5 px-3 py-2">
            <summary className="cursor-pointer text-sm">{item.title}</summary>
            <p className="mt-2 text-xs text-faint">{item.source_type}{item.files?.length ? ` · ${item.files.slice(0, 8).join(", ")}` : ""}</p>
            {item.summary && <p className="mt-2 text-sm text-mute">{item.summary}</p>}
            {item.diff && (
              <pre className="mt-2 max-h-48 overflow-auto text-[11px] leading-5 text-zinc-300">{item.diff.slice(0, 4000)}</pre>
            )}
            <button className="mt-2 text-xs text-mute underline" onClick={() => navigate(`/evidence?id=${item.id}`)}>打开证据页</button>
          </details>
        ))}
        {!detail?.evidence.length && <p className="text-sm text-faint">没有事实证据，不能启用。</p>}
      </section>

      <div className="flex flex-wrap gap-2">
        {compactActions && memory.status === "candidate" && (
          <>
            <Button disabled={saving || !detail?.evidence.length} onClick={() => save({ status: "active" }, "审核通过")}>启用</Button>
            <Button variant="secondary" disabled={saving} onClick={() => save({ status: "tentative" }, "先试用")}>试用</Button>
            <Button variant="danger" disabled={saving} onClick={() => save({ status: "rejected" }, "不像可复用经验")}>否决</Button>
          </>
        )}
        {compactActions && memory.status === "tentative" && (
          <>
            <Button disabled={saving || !detail?.evidence.length} onClick={() => save({ status: "active" }, "审核通过")}>启用</Button>
            <Button variant="danger" disabled={saving} onClick={() => save({ status: "rejected" }, "不像可复用经验")}>否决</Button>
          </>
        )}
        {!editing ? (
          <Button variant="secondary" onClick={() => setEditing(true)}>编辑</Button>
        ) : (
          <>
            <Button disabled={saving} onClick={() => {
              if (!reason.trim()) {
                toast.error("改内容时请写一句原因");
                return;
              }
              save();
            }}>{saving ? "保存中…" : "保存"}</Button>
            <Button variant="ghost" onClick={() => setEditing(false)}>取消</Button>
          </>
        )}
      </div>
    </div>
  );
}

function ReadBlock({ title, hint, items }: { title: string; hint: string; items: string[] }) {
  return (
    <section>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mt-1 text-xs text-faint">{hint}</p>
      <ul className="mt-2 grid gap-1 text-sm text-mute">
        {items.length ? items.map((item) => <li key={item}>{item}</li>) : <li className="text-faint">空</li>}
      </ul>
    </section>
  );
}

export function memorySummary(memory: Memory) {
  return memory.problem || "还没写清要解决什么";
}
