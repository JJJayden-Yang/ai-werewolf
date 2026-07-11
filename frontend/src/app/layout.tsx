import "./globals.css";

export const metadata = {
  title: "AI 狼人杀",
  description: "AI 狼人杀玩家回放与后台审计原型"
};

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <main className="app-shell">{children}</main>
      </body>
    </html>
  );
}
