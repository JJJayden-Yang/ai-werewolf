import Link from "next/link";

import { replayApi } from "@/lib/api";

import { ReplayArchiveClient } from "./ReplayArchiveClient";
import { ReplayLookupForm } from "./ReplayLookupForm";

export default async function ReplayListPage() {
  const replays = await replayApi.listReplays();

  return (
    <section className="archive-screen">
      <header className="archive-header">
        <Link className="home-link" href="/">
          返回主菜单
        </Link>
        <div>
          <p className="eyebrow">历史档案</p>
          <h1 className="page-title">选择一局回放</h1>
        </div>
      </header>

      <div className="archive-layout">
        <aside className="archive-filter">
          <h2>快速进入</h2>
          <p className="muted">输入已有对局编号，或选择右侧 demo 历史局。</p>
          <div className="lookup-wrap">
            <ReplayLookupForm />
          </div>
        </aside>

        <ReplayArchiveClient replays={replays} />
      </div>
    </section>
  );
}
