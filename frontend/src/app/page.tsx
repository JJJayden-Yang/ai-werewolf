import Link from "next/link";

export default function HomePage() {
  return (
    <section className="home-screen">
      <div className="home-status">
        <span className="status-pill online">服务在线</span>
        <span className="status-pill">V1</span>
      </div>

      <div className="home-board">
        <div className="ink-scene" aria-hidden="true" />

        <h1 className="ink-title">
          AI 狼人杀
          <span className="title-seal" aria-hidden="true">
            智趣
            <br />
            对战
          </span>
          <span className="title-art" aria-hidden="true" />
        </h1>

        <nav className="door-menu" aria-label="主菜单">
          <Link className="door-card door-red" href="/play">
            <strong>开始游戏</strong>
            <span className="door-crack" aria-hidden="true" />
          </Link>

          <Link className="door-card door-blue" href="/replay">
            <strong>历史回放</strong>
            <span className="door-crack" aria-hidden="true" />
          </Link>

          <Link
            className="door-card door-green"
            href="/admin/runs"
            aria-label="后台入口（需要密码）"
          >
            <strong>后台入口</strong>
          </Link>
        </nav>
      </div>
    </section>
  );
}
