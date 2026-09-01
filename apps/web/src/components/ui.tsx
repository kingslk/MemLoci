import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import { cva, type VariantProps } from "class-variance-authority";
import {
  ButtonHTMLAttributes,
  FormEvent,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";
import { cn } from "../lib/cn";

const buttonStyles = cva(
  "inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border px-4 py-2 text-sm font-medium transition duration-200 disabled:pointer-events-none",
  {
    variants: {
      variant: {
        primary: "border-white bg-white text-black hover:bg-zinc-200",
        secondary: "border-white/12 bg-[#0a0a0a] text-ink hover:border-white/20 hover:bg-white/6",
        ghost: "border-transparent text-mute hover:bg-white/6 hover:text-ink",
        danger: "border-transparent text-red-300 hover:bg-red-500/10",
      },
    },
    defaultVariants: { variant: "primary" },
  },
);

export function Button({
  children,
  className,
  variant,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonStyles>) {
  return (
    <button className={cn(buttonStyles({ variant }), className)} {...props}>
      {children}
    </button>
  );
}

const fieldControl =
  "w-full rounded-xl border border-white/12 bg-[#0a0a0a] px-3.5 py-2.5 text-sm text-ink outline-none transition placeholder:text-faint focus:border-white/25 focus:bg-white/[0.04]";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn(fieldControl, props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cn(fieldControl, "min-h-24 resize-y", props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cn(fieldControl, "appearance-none pr-8", props.className)} />;
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="grid gap-1.5">
      <span className="text-[13px] text-mute">{label}</span>
      {children}
      {hint && <span className="text-xs leading-5 text-faint">{hint}</span>}
    </label>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad";
}) {
  const tones = {
    neutral: "bg-white/8 text-mute",
    good: "bg-white text-black",
    warn: "bg-white/12 text-ink",
    bad: "bg-red-500/15 text-red-300",
  };
  return (
    <span className={cn("inline-flex rounded-full px-2.5 py-0.5 text-[11px] font-medium", tones[tone])}>
      {children}
    </span>
  );
}

export function Empty({ text }: { text: string }) {
  return <p className="px-4 py-16 text-center text-sm text-faint">{text}</p>;
}

export function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / Math.max(pageSize, 1)));
  if (total <= pageSize) return null;
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 text-xs text-faint">
      <span>共 {total} 条</span>
      <div className="flex items-center gap-2">
        <Button variant="ghost" className="px-2" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          上一页
        </Button>
        <span>
          {page} / {pages}
        </span>
        <Button variant="ghost" className="px-2" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          下一页
        </Button>
      </div>
    </div>
  );
}

export function PageHeader({
  kicker,
  title,
  description,
  actions,
}: {
  kicker?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-2xl">
        {kicker && <p className="text-xs text-faint">{kicker}</p>}
        <h2 className="mt-1 text-[2.5rem] font-semibold leading-none tracking-[-0.045em] md:text-[3rem]">{title}</h2>
        {description && <p className="mt-3 text-sm leading-6 text-mute">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

export function Card({
  children,
  className,
  hover,
}: {
  children: ReactNode;
  className?: string;
  hover?: boolean;
}) {
  return <div className={cn("card", hover && "card-hover", className)}>{children}</div>;
}

export function Dialog({
  open,
  title,
  children,
  onClose,
  onConfirm,
  confirmLabel = "确认",
  danger,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
  onConfirm: () => void;
  confirmLabel?: string;
  danger?: boolean;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm" />
        <DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-50 w-[min(28rem,calc(100%-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-3xl bg-[#111] p-6 shadow-[0_0_0_1px_rgba(255,255,255,0.08),0_24px_80px_rgba(0,0,0,0.6)]">
          <DialogPrimitive.Title className="text-lg font-medium tracking-tight">{title}</DialogPrimitive.Title>
          <div className="mt-3 text-sm leading-6 text-mute">{children}</div>
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} type="button">取消</Button>
            <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} type="button">{confirmLabel}</Button>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function Tip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip.Root delayDuration={200}>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side="right"
          sideOffset={10}
          className="z-50 rounded-lg bg-white px-2 py-1 text-xs font-medium text-black shadow-lg"
        >
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function TooltipProvider({ children }: { children: ReactNode }) {
  return <Tooltip.Provider delayDuration={200}>{children}</Tooltip.Provider>;
}

export function StatusTone(status: string): "neutral" | "good" | "warn" | "bad" {
  if (["active", "succeeded"].includes(status)) return "good";
  if (["tentative", "running", "syncing", "candidate"].includes(status)) return "warn";
  if (["failed", "rejected", "cancelled"].includes(status)) return "bad";
  return "neutral";
}

export function LineList({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
}) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.namedItem("item") as HTMLInputElement;
    const next = input.value.trim();
    if (!next) return;
    onChange([...value, next]);
    input.value = "";
  };
  return (
    <div className="grid gap-2">
      {value.map((item, index) => (
        <div key={`${item}-${index}`} className="flex items-center gap-2 rounded-2xl bg-white/5 px-3 py-2 text-sm ring-1 ring-white/8">
          <span className="flex-1">{item}</span>
          <button type="button" className="text-faint hover:text-ink" onClick={() => onChange(value.filter((_, i) => i !== index))}>
            删除
          </button>
        </div>
      ))}
      <form className="flex gap-2" onSubmit={submit}>
        <Input name="item" placeholder={placeholder ?? "回车添加一项"} />
        <Button variant="secondary" type="submit">添加</Button>
      </form>
    </div>
  );
}
