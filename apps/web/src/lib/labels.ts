export const statusLabels: Record<string, string> = {
  queued: "等待执行",
  running: "执行中",
  paused: "已暂停",
  cancel_requested: "正在取消",
  skipped_llm: "部分调用失败",
  cancelled: "已取消",
  retryable: "可重试",
  failed: "失败",
  succeeded: "成功",
  syncing: "同步中",
  pending: "等待处理",
  candidate: "待审",
  tentative: "试用中",
  active: "已启用",
  deprecated: "已过时",
  archived: "已归档",
  rejected: "已否决",
};

export const stageLabels: Record<string, string> = {
  current_state_scan: "扫描当前代码",
  full_history_scan: "导入完整历史",
  architecture_epochs: "分析架构阶段",
  topic_reconstruction: "重建主题",
  genesis_dream: "整理初始记忆",
  completed: "已完成",
  queued: "等待执行",
};

export const jobKindLabels: Record<string, string> = {
  full_initialization: "全量初始化",
  history_sync: "导入历史证据",
  mirror_sync: "同步代码仓库",
  memory_polish: "打磨近邻记忆",
  dream_incremental: "整理新变更",
  dream_manual: "重新整理",
  dream_genesis: "首次整理",
  dream_full_validation: "全量校验",
};

export const dreamTypeLabels: Record<string, string> = {
  incremental: "整理新变更",
  manual: "重新整理",
  genesis: "首次整理",
  full_validation: "全量校验",
};

export const fieldHints = {
  title: "一句话经验名，不要用 commit 原句",
  problem: "这是什么工程麻烦，不是变更摘要",
  pattern: "换一个仓库也能照着做的步骤",
  implementation: "来源仓库里的具体链路，仅供对照",
  do_not_copy: "来源仓库特有的目录、框架、命名，换项目不要抄",
  apply_when: "当前任务满足这些条件再启用",
  do_not: "会扩大范围或套错架构时不要用",
};

export const allowedTransitions: Record<string, string[]> = {
  candidate: ["tentative", "rejected", "active"],
  tentative: ["active", "rejected", "candidate"],
  active: ["deprecated"],
  deprecated: ["archived", "active"],
  archived: [],
  rejected: ["candidate"],
};

export function jobActivity(job: {
  current_stage?: string;
  progress: number;
  checkpoint?: {
    detail?: string;
    completed?: number;
    total?: number;
  };
}) {
  const detail = job.checkpoint?.detail || showStage(job.current_stage || "queued");
  const counts =
    job.checkpoint?.completed != null && job.checkpoint?.total
      ? `${job.checkpoint.completed}/${job.checkpoint.total}`
      : null;
  const percent = `${Math.round(job.progress * 100)}%`;
  return [detail, counts, percent].filter(Boolean).join(" · ");
}

const finishTime = new Intl.DateTimeFormat("zh-CN", {
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function jobEta(
  job: {
    status: string;
    progress: number;
    started_at?: string | null;
    finished_at?: string | null;
  },
  now = Date.now(),
) {
  if (["succeeded", "failed", "cancelled"].includes(job.status)) {
    return job.finished_at ? `结束于 ${finishTime.format(new Date(job.finished_at))}` : "已结束";
  }
  if (job.status === "queued") return "预计完成时间：等待执行后计算";
  if (job.status === "paused") return "预计完成时间：任务已暂停";
  if (!job.started_at || job.progress <= 0.01) return "预计完成时间：计算中";
  const started = new Date(job.started_at).getTime();
  const elapsed = Math.max(now - started, 1);
  const progress = Math.min(Math.max(job.progress, 0.01), 0.99);
  const estimated = now + elapsed * (1 - progress) / progress;
  if (!Number.isFinite(estimated) || estimated <= now) return "预计完成时间：重新计算中";
  return `预计完成：${finishTime.format(new Date(estimated))}`;
}

export const showStatus = (value: string) => statusLabels[value] ?? value;
export const showStage = (value: string) => stageLabels[value] ?? value;
export const showDreamType = (value: string) => dreamTypeLabels[value] ?? value;
export const showJobKind = (value: string) => jobKindLabels[value] ?? value;

export const callableStatus = new Set(["active", "tentative"]);
export const reviewStatus = new Set(["candidate", "tentative"]);
export const libraryStatus = new Set(["active", "deprecated", "archived"]);
