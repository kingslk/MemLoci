import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  BookMarked,
  ChevronDown,
  FileSearch,
  FolderGit2,
  GitBranch,
  LayoutDashboard,
  ListTodo,
  Network,
  ScrollText,
  Settings,
  Sparkles,
} from "lucide-react";
import { jobActivity } from "../lib/labels";
import { useWorkspace } from "../workspace";
import { Button, TooltipProvider } from "./ui";

const links = [
  { to: "/", label: "概览", icon: LayoutDashboard, end: true },
  { to: "/review", label: "待审", icon: ListTodo },
  { to: "/memories", label: "记忆库", icon: BookMarked },
  { to: "/evidence", label: "证据", icon: FileSearch },
  { to: "/graph", label: "图谱", icon: Network },
  { to: "/query-logs", label: "召回日志", icon: ScrollText },
  { to: "/dreams", label: "整理记录", icon: Sparkles },
  { to: "/repositories", label: "仓库", icon: FolderGit2 },
  { to: "/jobs", label: "任务", icon: GitBranch },
  { to: "/settings", label: "设置", icon: Settings },
];

export function Shell() {
  const { projects, project, projectId, setProjectId, runningJob, hasToken } = useWorkspace();
  const navigate = useNavigate();

  return (
    <TooltipProvider>
      <div className="grok-glow flex min-h-screen text-ink">
        <aside className="fixed inset-x-0 bottom-0 z-40 flex h-16 items-center border-t border-white/10 bg-[#050505] px-2 md:sticky md:top-0 md:h-screen md:w-[88px] md:shrink-0 md:flex-col md:border-r md:border-t-0 md:px-0 md:py-5">
          <div className="mb-8 hidden size-10 place-items-center rounded-full bg-white text-sm font-semibold text-black md:grid">
            M
          </div>
          <nav className="flex min-w-0 flex-1 items-center justify-between overflow-hidden md:flex-col md:justify-start md:gap-1 md:overflow-visible">
            {links.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                aria-label={item.label}
                title={item.label}
                className={({ isActive }) =>
                  `grid size-10 shrink-0 place-items-center rounded-xl border transition md:size-12 ${
                    isActive
                      ? "border-white/12 bg-white/12 text-white"
                      : "border-transparent text-mute hover:border-white/8 hover:bg-white/5 hover:text-ink"
                  }`
                }
              >
                <item.icon size={18} strokeWidth={1.7} />
              </NavLink>
            ))}
          </nav>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex min-h-20 items-center justify-between gap-4 px-4 py-4 md:px-8">
            <Dropdown.Root>
              <Dropdown.Trigger asChild>
                <button className="inline-flex min-h-10 items-center gap-3 rounded-xl border border-white/12 bg-[#090909] px-4 text-sm font-medium text-ink transition hover:border-white/20 hover:bg-white/5">
                  <span className="max-w-48 truncate">{project?.name ?? "选择项目"}</span>
                  <ChevronDown size={14} className="text-faint" />
                </button>
              </Dropdown.Trigger>
              <Dropdown.Portal>
                <Dropdown.Content
                  align="start"
                  sideOffset={8}
                  className="z-50 min-w-48 rounded-xl border border-white/12 bg-[#0a0a0a] p-1 shadow-2xl"
                >
                  {projects.map((item) => (
                    <Dropdown.Item
                      key={item.id}
                      className={`cursor-pointer rounded-lg px-3 py-2 text-sm outline-none hover:bg-white/8 ${item.id === projectId ? "text-white" : "text-mute"}`}
                      onSelect={() => setProjectId(item.id)}
                    >
                      {item.name}
                    </Dropdown.Item>
                  ))}
                  <Dropdown.Separator className="my-1 h-px bg-white/8" />
                  <Dropdown.Item
                    className="cursor-pointer rounded-lg px-3 py-2 text-sm text-mute outline-none hover:bg-white/8"
                    onSelect={() => navigate("/settings")}
                  >
                    管理项目…
                  </Dropdown.Item>
                </Dropdown.Content>
              </Dropdown.Portal>
            </Dropdown.Root>
            {runningJob && (
              <div className="flex items-center gap-3 text-xs text-mute">
                <span className="max-w-[36rem] truncate">任务 #{runningJob.id} · {jobActivity(runningJob)}</span>
                <span className="h-1 w-28 overflow-hidden rounded-full bg-white/10">
                  <span className="block h-full bg-white" style={{ width: `${Math.round(runningJob.progress * 100)}%` }} />
                </span>
              </div>
            )}
          </header>

          {!hasToken && (
            <div className="mx-4 mb-6 flex items-center justify-between rounded-xl border border-white/12 bg-[#0a0a0a] px-5 py-4 text-sm md:mx-8">
              <span className="text-mute">还没填写管理令牌，创建、审核、整理都会失败。</span>
              <Button className="shrink-0 whitespace-nowrap" variant="secondary" onClick={() => navigate("/settings")}>去设置</Button>
            </div>
          )}

          <main className="min-w-0 flex-1 overflow-x-hidden px-4 pb-24 md:px-8 md:pb-16">
            <div className="mx-auto w-full min-w-0 max-w-[1360px]">
              <Outlet />
            </div>
          </main>
        </div>
      </div>
    </TooltipProvider>
  );
}
