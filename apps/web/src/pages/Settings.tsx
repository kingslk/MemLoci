import { FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button, Card, Field, Input, PageHeader } from "../components/ui";
import { useWorkspace } from "../workspace";

export function SettingsPage() {
  const { project, saveProject, createProject, setToken } = useWorkspace();
  const [tokenDraft, setTokenDraft] = useState(
    () => window.sessionStorage.getItem("memloci_admin_token") ?? "",
  );
  const [edit, setEdit] = useState({ name: "", description: "" });
  const [create, setCreate] = useState({ name: "", description: "" });

  useEffect(() => {
    if (!project) return;
    setEdit({ name: project.name, description: project.description });
  }, [project]);

  const saveToken = (event: FormEvent) => {
    event.preventDefault();
    setToken(tokenDraft);
    toast.success("管理令牌已保存在当前标签页");
  };

  return (
    <div className="grid max-w-xl gap-6">
      <PageHeader kicker="设置" title="项目和令牌" description="令牌只存在当前标签页。关掉浏览器就丢。" />
      <Card className="grid gap-3 p-6">
        <form className="grid gap-3" onSubmit={saveToken}>
          <Field label="管理令牌" hint="对应 .env 里的 ADMIN_TOKEN。保存后会重新拉数据。">
            <Input type="password" value={tokenDraft} onChange={(event) => setTokenDraft(event.target.value)} />
          </Field>
          <Button type="submit">保存令牌</Button>
        </form>
      </Card>
      {project && (
        <Card className="p-6">
          <form
            className="grid gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              saveProject(edit);
            }}
          >
            <h3 className="text-lg font-medium">当前项目</h3>
            <Field label="名称"><Input required value={edit.name} onChange={(event) => setEdit({ ...edit, name: event.target.value })} /></Field>
            <Field label="描述"><Input value={edit.description} onChange={(event) => setEdit({ ...edit, description: event.target.value })} /></Field>
            <Button type="submit">保存项目</Button>
          </form>
        </Card>
      )}
      <Card className="p-6">
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            createProject(create);
            setCreate({ name: "", description: "" });
          }}
        >
          <h3 className="text-lg font-medium">新建项目</h3>
          <Field label="名称"><Input required value={create.name} onChange={(event) => setCreate({ ...create, name: event.target.value })} /></Field>
          <Field label="描述"><Input value={create.description} onChange={(event) => setCreate({ ...create, description: event.target.value })} /></Field>
          <Button type="submit">创建项目</Button>
        </form>
      </Card>
    </div>
  );
}
